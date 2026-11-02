"""会员权益与就医路径白名单。

架构 §4.4 的红线落地：任何涉及付费判断的分支，先判断当前动作是否属于就医路径，
是则直接放行，**不读会员状态**。

三道保障的前两道在这里：
1. 路由装饰器显式声明 action，未声明直接 500（见 deps.require_action + main 的启动自检）
2. MEDICAL_ACTIONS 是本文件唯一真相源，与 /v1/config/medical-actions 返回同一份数据
3. 契约测试在 tests/test_contract.py，覆盖「未开通 / 已过期 / 已封禁」全部断言 200
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import now
from ..deps import CurrentUser, DbDep, register_checks, require_action
from ..errors import ApiError, Code

router = APIRouter(tags=["membership"])

# 变更时递增，客户端据此刷新本地缓存。与契约的 medical-actions.version 保持一致。
ACTIONS_VERSION = "2026-10-27.1"

# 服务端永远放行的动作，两类：
#   1. 就医路径（PRD §1.4）：预问诊、科室推荐、医生、下单支付、红旗急救、多档案、
#      时间线、记忆管理 —— 永久免费、永不弹付费引导、永不做限流降级
#   2. 自有数据读写（PRD §4.9）：会员过期只收回增强能力，不收回数据，
#      所以列表、详情、删除一律免费
# 可以收费的只有「消耗型」动作：对话轮次、报告解读次数、提醒条数、自定义角色。
MEDICAL_ACTIONS = frozenset(
    {
        "patient.list",
        "patient.create",
        "patient.read",
        "patient.update",
        "patient.delete",
        "persona.list",
        "preconsult.start",
        "preconsult.answer",
        "preconsult.finish",
        "preconsult.confirm",
        "match.departments",
        "department.list",
        "doctor.list",
        "doctor.detail",
        "order.create",
        "order.read",
        "order.pay",
        "safety.redflag",
        "safety.crisis",
        "timeline.read",
        "timeline.delete",
        "timeline.export",
        "memory.read",
        "memory.update",
        "memory.delete",
        "chat.history",
        # 会话列表与归档：读/整理自己的对话记录，属于 PRD §4.9「会员过期不收回数据」
        "conversations.list",
        "conversations.archive",
        "reminder.pending",
        "reminder.delete",
        "report.read",
        "membership.read",
        "me.update",
        "config.medical_actions",
        "config.redflag_rules",
    }
)

# 可以收费的部分（不在白名单里，受额度约束）
FREE_CHAT_DAILY = 20
MEMBER_CHAT_DAILY = 200  # PRD §15.4「无限对话」实为公平使用，日 ≤200
FREE_REMINDER_MAX = 3
FREE_REPORT_MONTHLY = 3
CUSTOM_PERSONA_QUOTA = 3  # 会员附赠或 9 元单买，各 3 个名额
MAX_PATIENTS = 6  # 会员：本人 + 5 位家人
MAX_PATIENTS_FREE = 3  # 免费：本人 + 2 位家人。多建档仍是就医路径，只是条数有别


def is_medical_action(action: str) -> bool:
    return action in MEDICAL_ACTIONS


def get_membership(db: Session, user_id: str) -> models.Membership:
    """永远返回一行。status=active 要求 plan 有效且未过期。"""
    membership = db.scalar(select(models.Membership).where(models.Membership.user_id == user_id))
    if membership is None:
        membership = models.Membership(
            user_id=user_id, plan="none", status="none", end_at=now() - timedelta(days=1)
        )
        db.add(membership)
        db.flush()
    if membership.status == "active" and membership.end_at is not None:
        if membership.end_at <= now():
            # 过期只收回增强能力，不收回数据和就医路径（PRD §4.9）
            membership.status = "expired"
            db.flush()
    return membership


def is_member(db: Session, user_id: str) -> bool:
    return get_membership(db, user_id).status == "active"


def entitlements_of(membership: models.Membership) -> dict:
    member = membership.status == "active"
    return {
        "daily_chat_limit": MEMBER_CHAT_DAILY if member else FREE_CHAT_DAILY,
        "custom_persona_quota": CUSTOM_PERSONA_QUOTA if member else 0,
        "max_patients": MAX_PATIENTS if member else MAX_PATIENTS_FREE,
        "max_reminders": None if member else FREE_REMINDER_MAX,  # null = 不限
        "monthly_report_interpret": None if member else FREE_REPORT_MONTHLY,
        "priority_handoff": member,
        "family_annual_report": member,
        "history_report_compare": member,
    }


def membership_out(membership: models.Membership) -> schemas.MembershipOut:
    expires_at = membership.end_at or (now() - timedelta(days=1))
    return schemas.MembershipOut(
        status=membership.status,  # type: ignore[arg-type]
        plan=membership.plan,  # type: ignore[arg-type]
        expires_at=expires_at,
        entitlements=entitlements_of(membership),
    )


# ------------------------------------------------------------------ 限额

# ponytail: 内存计数，单实例够用。多实例部署时换 Redis INCR，接口不变。
_rate_buckets: dict[str, list[datetime]] = {}


def _hit(bucket: str, key: str, limit: int, window_seconds: int) -> None:
    stamp = now()
    marks = [
        t
        for t in _rate_buckets.setdefault(f"{bucket}:{key}", [])
        if (stamp - t).total_seconds() < window_seconds
    ]
    if len(marks) >= limit:
        raise ApiError(
            Code.RATE_LIMITED,
            f"操作太频繁了，请 {window_seconds} 秒后再试",
            http_status=429,
            details={"retry_after_seconds": window_seconds},
        )
    marks.append(stamp)
    _rate_buckets[f"{bucket}:{key}"] = marks


# --- 各动作的前置检查。签名统一为 (db, user)，由 deps.require_action 调用 ---


def rate_limit_sms(phone: str) -> None:
    """架构 §4.2：发送验证码限流 1 次/60s。按手机号限，登录前没有 user 可用。"""
    _hit("sms", phone, limit=1, window_seconds=60)


def _rate_persona(db: Session, user: models.User) -> None:
    """契约：创建自定义角色每分钟最多 3 次。"""
    _hit("persona", user.id, limit=3, window_seconds=60)


def check_chat_quota(db: Session, user: models.User) -> None:
    """限额只限轮数，不限功能（契约测试 #8：用尽后对话仍可进行）。"""
    limit = entitlements_of(get_membership(db, user.id))["daily_chat_limit"]
    since = now() - timedelta(days=1)
    used = (
        db.query(models.AiLog)
        .filter(models.AiLog.user_id == user.id, models.AiLog.scene == "chat")
        .filter(models.AiLog.created_at >= since)
        .count()
    )
    if used >= limit:
        # http 用默认 400，**不能是 200**：契约里错误走 default（非 2xx），而客户端
        # 按状态码判成败。返回 200 + 错误体，客户端会把错误体当成成功对象 resolve
        # （踩过：免费用户建角色拿到 id=undefined，接着拿它上传头像 → "角色不存在"）。
        # 「不是付费墙」指的是别用 402 和付费专用码，业务码 PERSONA_*_EXCEEDED 照给。
        raise ApiError(
            Code.CHAT_DAILY_LIMIT_EXCEEDED,
            f"今天的 {limit} 轮对话用完了。可以明天再来，也可以直接找医生，或看看相关知识。",
            details={"limit": limit, "reset_at": _next_midnight().isoformat()},
        )


def _next_midnight() -> datetime:
    stamp = now()
    return stamp.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def check_reminder_quota(db: Session, user: models.User) -> None:
    limit = entitlements_of(get_membership(db, user.id))["max_reminders"]
    if limit is None:
        return
    used = (
        db.query(models.Reminder)
        .filter(models.Reminder.user_id == user.id, models.Reminder.status == "pending")
        .count()
    )
    if used >= limit:
        raise ApiError(
            Code.REMINDER_LIMIT_EXCEEDED,
            f"免费版最多 {limit} 条提醒。删除一条，或升级会员解锁不限条数。",
            details={"limit": limit},
        )


def check_persona_quota(db: Session, user: models.User) -> None:
    quota = entitlements_of(get_membership(db, user.id))["custom_persona_quota"]
    used = (
        db.query(models.Persona)
        .filter(models.Persona.owner_user_id == user.id, models.Persona.is_preset.is_(False))
        .count()
    )
    if used >= quota:
        # 契约明确：这里返回业务码，**不做付费拦截**（付费在客户端侧引导）
        # 文案分两种：免费版从来没名额（说「用完了」是骗人），会员才是真用完了。
        # http 状态用默认 400——不能是 200，理由见 check_chat_quota 上面的注释。
        message = (
            "自定义角色名额用完了。会员附赠 3 个名额，也可以单独购买。"
            if quota > 0
            else "自定义角色需要会员。开通会员附赠 3 个名额，也可以单独购买。"
        )
        raise ApiError(
            Code.PERSONA_QUOTA_EXCEEDED,
            message,
            details={"quota": quota, "used": used},
        )


def check_report_quota(db: Session, user: models.User) -> None:
    limit = entitlements_of(get_membership(db, user.id))["monthly_report_interpret"]
    if limit is None:
        return
    month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (
        db.query(models.ReportDoc)
        .filter(models.ReportDoc.user_id == user.id, models.ReportDoc.created_at >= month_start)
        .count()
    )
    if used >= limit:
        raise ApiError(
            Code.REPORT_MONTHLY_LIMIT_EXCEEDED,
            f"免费版每月 {limit} 次报告解读。升级会员后不限次数。",
            details={"limit": limit},
        )


# 注册给 deps.require_action。就医路径动作不出现在这里，也就不会被任何检查拦住。
# auth.sms 不注册：它按手机号限流，而登录前还没有 user。
register_checks("chat.stream", check_chat_quota)
# persona.create 只挂频控。额度检查在 persona 模块里内容审核**之后**跑，
# 否则免费用户永远看不到「名字里有身份词」的提示。
register_checks("persona.create", _rate_persona)
register_checks("reminder.create", check_reminder_quota)
register_checks("report.upload", check_report_quota)


# ------------------------------------------------------------------ 端点


@router.get("/v1/membership", response_model=schemas.MembershipOut)
def read_membership(
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("membership.read")),
) -> schemas.MembershipOut:
    return membership_out(get_membership(db, user.id))


@router.get("/v1/config/medical-actions", response_model=schemas.MedicalActions)
def read_medical_actions(_: None = Depends(require_action("config.medical_actions"))) -> schemas.MedicalActions:
    """权威全量清单，来自 MEDICAL_ACTIONS，不是手写副本。"""
    return schemas.MedicalActions(actions=sorted(MEDICAL_ACTIONS), version=ACTIONS_VERSION)


@router.get("/v1/redflag/rules", response_model=schemas.RedFlagRules)
def read_redflag_rules(
    db: DbDep,
    _: None = Depends(require_action("config.redflag_rules")),
) -> schemas.RedFlagRules:
    """客户端缓存后做离线预检；服务端仍是最终判定方。"""
    rules = list(db.scalars(select(models.RedFlagRule).where(models.RedFlagRule.enabled.is_(True))))
    version = max((r.version for r in rules), default="1")
    return schemas.RedFlagRules(
        version=version,
        rules=[
            schemas.RedFlagRuleOut(
                id=r.id, keywords=r.keywords, level=r.level, action=r.action, message=r.message  # type: ignore[arg-type]
            )
            for r in rules
        ],
    )


__all__ = [
    "ACTIONS_VERSION",
    "CUSTOM_PERSONA_QUOTA",
    "MAX_PATIENTS",
    "MAX_PATIENTS_FREE",
    "MEDICAL_ACTIONS",
    "check_chat_quota",
    "check_persona_quota",
    "check_reminder_quota",
    "check_report_quota",
    "entitlements_of",
    "get_membership",
    "is_member",
    "is_medical_action",
    "membership_out",
    "rate_limit_sms",
    "router",
]
