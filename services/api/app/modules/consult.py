"""咨询订单状态机与支付。

状态机（PRD §7.2）：
  待支付 ──支付──> 待接诊 ──医生接单──> 已接诊 ──提交回复──> 已完成 ──评价──> 已评价

金额拆 price / platform_fee / doctor_income / tax_amount，结算可对账（架构 §3.2）。
税按劳务报酬代扣代缴，settle_ratio 是医生分成比例。

超时分支（30min 未付关闭、24h 未接单退款、48h 未回复退款+扣分）由定时任务处理，
本文件提供 sweep_expired_orders() 供 job 调度器调用。
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, require_action
from ..errors import ApiError, Code, not_found
from ..modules.match import get_doctor_or_404
from ..modules.preconsult import get_owned_session
from ..modules.profile import get_owned_patient

router = APIRouter(tags=["order"])

PLATFORM_FEE_RATE = 0.2  # 平台服务费 20%
TAX_RATE = 0.05  # 劳务报酬代扣代缴（示意，实际按累进税率）
PAY_TIMEOUT_MINUTES = 30
ACCEPT_TIMEOUT_HOURS = 24
REPLY_TIMEOUT_HOURS = 48


def _split_amount(price_cents: int, settle_ratio: float) -> tuple[int, int, int]:
    """返回 (platform_fee, doctor_income, tax)，单位分，保证三者相加 == price。"""
    fee = round(price_cents * PLATFORM_FEE_RATE)
    gross = price_cents - fee
    doctor_income = round(gross * settle_ratio)
    tax = gross - doctor_income
    return fee, doctor_income, tax


def _order_no() -> str:
    stamp = now().strftime("%Y%m%d%H%M%S")
    return f"ML{stamp}{secrets.token_hex(3).upper()}"


def _to_out(order: models.ConsultOrder) -> schemas.OrderOut:
    return schemas.OrderOut(
        id=order.id,
        status=order.status,  # type: ignore[arg-type]
        amount_cents=order.price_cents,
        doctor_id=order.doctor_id,
        patient_id=order.patient_id,
        pay_params=order.pay_params,
        created_at=order.created_at,
        paid_at=order.paid_at,
    )


def get_owned_order(db: Session, user_id: str, order_id: str) -> models.ConsultOrder:
    order = db.get(models.ConsultOrder, order_id)
    if order is None or order.user_id != user_id:
        raise not_found("订单")
    return order


@router.post("/v1/orders", status_code=201, response_model=schemas.OrderOut)
def create_order(
    payload: schemas.OrderCreate,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("order.create")),
) -> schemas.OrderOut:
    patient = get_owned_patient(db, user.id, payload.patient_id)
    doctor = get_doctor_or_404(db, payload.doctor_id)

    if payload.preconsult_session_id:
        # 病历必须属于同一个就诊人，防止拿别人的病历下单
        session = get_owned_session(db, user.id, payload.preconsult_session_id)
        if session.patient_id != patient.id:
            raise ApiError(Code.VALIDATION_ERROR, "病历与就诊人不匹配")

    fee, doctor_income, tax = _split_amount(doctor.price_cents, doctor.settle_ratio)
    order = models.ConsultOrder(
        id=new_id(),
        order_no=_order_no(),
        user_id=user.id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        pre_consult_id=payload.preconsult_session_id,
        price_cents=doctor.price_cents,
        platform_fee_cents=fee,
        doctor_income_cents=doctor_income,
        tax_cents=tax,
        status="pending_pay",
        created_at=now(),
    )
    db.add(order)
    db.flush()
    return _to_out(order)


@router.get("/v1/orders/{order_id}", response_model=schemas.OrderOut)
def read_order(
    order_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("order.read")),
) -> schemas.OrderOut:
    return _to_out(get_owned_order(db, user.id, order_id))


@router.post("/v1/orders/{order_id}/pay", response_model=schemas.OrderOut)
def pay_order(
    order_id: str,
    *,
    payload: schemas.OrderPay | None = None,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("order.pay")),
) -> schemas.OrderOut:
    channel = (payload.channel if payload else None) or "wechat"
    order = get_owned_order(db, user.id, order_id)

    if order.status != "pending_pay":
        # 幂等：已支付的重复调用直接返回当前状态，不重复生成支付单
        return _to_out(order)

    order.channel = channel
    order.pay_params = {
        "channel": channel,
        "order_no": order.order_no,
        "amount_cents": order.price_cents,
        # ponytail: 假支付参数。接真实渠道时换成微信/支付宝的 prepay 响应，只改这一段。
        "prepay_id": f"sandbox_{secrets.token_hex(12)}",
    }

    if get_settings().payment_mock:
        # 沙箱：直接置 paid，跑通接单与时间线。生产必须 PAYMENT_MOCK=false，
        # 状态由支付渠道的异步回调推进。
        order.status = "paid"
        order.paid_at = now()
        _write_paid_timeline(db, order)

    db.flush()
    return _to_out(order)


def _write_paid_timeline(db: Session, order: models.ConsultOrder) -> None:
    doctor = db.get(models.Doctor, order.doctor_id)
    db.add(
        models.TimelineEvent(
            patient_id=order.patient_id,
            user_id=order.user_id,
            type="consult",
            ref_id=order.id,
            title=f"已预约 {doctor.name if doctor else ''} 医生咨询",
            summary=f"订单号 {order.order_no}，等待医生接诊",
            occurred_at=now(),
        )
    )


def sweep_expired_orders(db: Session) -> dict[str, int]:
    """定时任务入口：处理 30min / 24h / 48h 三个超时分支。

    ponytail: 全表扫。日单量上千后改成按 created_at / paid_at 加索引的范围查询。
    """
    stamp = now()
    stats = {"cancelled": 0, "refunded": 0}
    pending = list(
        db.query(models.ConsultOrder).filter(models.ConsultOrder.status == "pending_pay").all()
    )
    for order in pending:
        if stamp - order.created_at > timedelta(minutes=PAY_TIMEOUT_MINUTES):
            order.status = "cancelled"
            stats["cancelled"] += 1

    accepted = list(
        db.query(models.ConsultOrder).filter(models.ConsultOrder.status == "paid").all()
    )
    for order in accepted:
        if order.paid_at and stamp - order.paid_at > timedelta(hours=ACCEPT_TIMEOUT_HOURS):
            order.status = "refunded"
            stats["refunded"] += 1

    replying = list(
        db.query(models.ConsultOrder).filter(models.ConsultOrder.status == "doctor_replying").all()
    )
    for order in replying:
        if order.paid_at and stamp - order.paid_at > timedelta(hours=REPLY_TIMEOUT_HOURS):
            order.status = "refunded"
            stats["refunded"] += 1
    return stats
