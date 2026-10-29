"""打卡、报告、时间线、就诊摘要导出。

报告 OCR 是异步的（契约 202 Accepted）。没有引消息队列，用 BackgroundTasks
跑 OCR；换 arq / celery 时只改 run_ocr 的调度方式，接口不变。

时间线只聚合「有结论」的事件，不堆原始对话（PRD §8.5）。
导出的摘要带 7 天有效期与不可枚举的 token，页脚固定免责句。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, PageQuery, paginate, require_action, require_public_action
from ..errors import ApiError, Code, not_found
from ..platform import storage
from ..modules.profile import get_owned_patient

log = logging.getLogger("minglu")
router = APIRouter(tags=["health"])

EXPORT_FOOTER = "本摘要由用户自行整理，供就诊参考，不构成诊断。"


@router.post("/v1/reports", status_code=202, response_model=schemas.ReportAccepted)
async def upload_report(
    *,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("report.upload")),
) -> schemas.ReportAccepted:
    get_owned_patient(db, user.id, patient_id)

    content_type = (file.content_type or "").lower()
    if content_type not in storage.ALLOWED_IMAGE_TYPES:
        raise ApiError(Code.UPLOAD_BAD_TYPE, "只支持 jpg / png / webp 图片")
    content = await file.read()
    if len(content) > get_settings().upload_max_bytes:
        raise ApiError(
            Code.UPLOAD_TOO_LARGE,
            f"图片不能超过 {get_settings().upload_max_bytes // 1024 // 1024}MB",
        )

    relpath = storage.save_upload(
        content, f"{new_id()}.{storage.ALLOWED_IMAGE_TYPES[content_type]}", "reports"
    )
    report = models.ReportDoc(
        id=new_id(),
        patient_id=patient_id,
        user_id=user.id,
        image_path=relpath,
        image_url=storage.public_url(relpath),
        status="processing",
        created_at=now(),
    )
    db.add(report)
    db.flush()

    background.add_task(run_ocr, report.id)
    return schemas.ReportAccepted(report_id=report.id, status="processing")


def run_ocr(report_id: str) -> None:
    """在独立会话里跑 OCR，写完即关。失败置 failed，提示重拍或手动录入（PRD §14 #10）。"""
    from ..database import get_sessionmaker

    session = get_sessionmaker()()
    try:
        report = session.get(models.ReportDoc, report_id)
        if report is None:
            return
        try:
            items = _ocr(report)
        except Exception:  # noqa: BLE001 - OCR 失败不能影响主请求
            log.exception("ocr failed report=%s", report_id)
            report.status = "failed"
            report.ai_comment = "识别失败，请重拍，或手动录入指标。"
        else:
            report.items = [item.model_dump() for item in items]
            report.abnormal_items = [item.name for item in items if item.flag != "normal"]
            report.status = "done"
            report.ai_comment = _comment(items)
        session.commit()
    finally:
        session.close()


def _ocr(report: models.ReportDoc) -> list[schemas.ReportItem]:
    """OCR 提供者。未接入时返回空列表，由调用方标 failed。

    ponytail: 接境内 OCR 服务时只替换这个函数，返回 ReportItem 列表即可，
    参考范围比对与解读在 _comment 里。
    """
    if not storage.exists(report.image_path):
        raise RuntimeError("报告图片已丢失")
    return []


def _comment(items: list[schemas.ReportItem]) -> str:
    if not items:
        return ""
    abnormal = [i for i in items if i.flag != "normal"]
    head = (
        "已识别出偏离参考范围的指标：" + "、".join(i.name for i in abnormal)
        if abnormal
        else "各项指标都在参考范围内。"
    )
    return f"{head}（AI 生成，仅供参考，具体请咨询医生或复查）"


@router.get("/v1/reports/{report_id}", response_model=schemas.ReportOut)
def read_report(
    report_id: str,
    *,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("report.read")),
) -> schemas.ReportOut:
    report = db.get(models.ReportDoc, report_id)
    if report is None or report.user_id != user.id:
        raise not_found("报告")
    return schemas.ReportOut(
        id=report.id,
        patient_id=report.patient_id,
        status=report.status,  # type: ignore[arg-type]
        image_url=report.image_url,
        items=[schemas.ReportItem.model_validate(i) for i in (report.items or [])] or None,
        summary=report.ai_comment,
        created_at=report.created_at,
    )


@router.get("/v1/timeline", response_model=schemas.TimelineList)
def read_timeline(
    *,
    db: DbDep,
    user: CurrentUser,
    patient_id: str,
    kind: str | None = None,
    page: PageQuery = Depends(),
    _: None = Depends(require_action("timeline.read")),
) -> schemas.TimelineList:
    get_owned_patient(db, user.id, patient_id)
    stmt = select(models.TimelineEvent).where(
        models.TimelineEvent.user_id == user.id,
        models.TimelineEvent.patient_id == patient_id,
        models.TimelineEvent.hidden.is_(False),
    )
    if kind:
        stmt = stmt.where(models.TimelineEvent.type == kind)
    stmt = stmt.order_by(models.TimelineEvent.occurred_at.desc())
    items, total, has_more = paginate(db, stmt, page)
    return schemas.TimelineList(
        items=[schemas.TimelineEventOut.model_validate(e) for e in items],
        total=total,
        has_more=has_more,
    )


@router.delete("/v1/timeline/{event_id}", status_code=204)
def delete_timeline_event(
    event_id: str,
    *,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("timeline.delete")),
) -> None:
    """删除后不再出现在时间线，也不再进导出摘要。"""
    event = db.get(models.TimelineEvent, event_id)
    if event is None or event.user_id != user.id:
        raise not_found("时间线事件")
    db.delete(event)
    db.flush()


@router.post("/v1/timeline/export", status_code=201, response_model=schemas.TimelineExportResult)
def export_timeline(
    payload: schemas.TimelineExport,
    *,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("timeline.export")),
) -> schemas.TimelineExportResult:
    patient = get_owned_patient(db, user.id, payload.patient_id)
    body, filled = _render_summary(db, patient, payload)
    if filled == 0:
        # 空摘要发给医生没有意义，还会让人以为数据丢了。契约 1.3.1 起明确 400。
        raise ApiError(
            Code.VALIDATION_ERROR,
            "这个时间段还没有健康记录。先聊几句、或做一次预问诊，再导出。",
        )

    token = new_id()
    relpath = storage.write_text(f"exports/{token}.txt", body)
    expires_at = now() + timedelta(days=get_settings().export_ttl_days)
    db.add(
        models.ShareExport(
            token=token,
            patient_id=patient.id,
            user_id=user.id,
            file_path=relpath,
            created_at=now(),
            expires_at=expires_at,
        )
    )
    db.flush()
    return schemas.TimelineExportResult(
        url=f"{get_settings().public_base_url.rstrip('/')}/v1/exports/{token}",
        expires_at=expires_at,
    )


@router.get("/v1/exports/{token}")
def read_export(
    token: str,
    *,
    db: DbDep,
    _: None = Depends(require_public_action("export.read")),
) -> PlainTextResponse:
    """公开端点，靠 URL 里的 token 鉴权。过期返回 410，页面展示「已失效」。"""
    record = db.get(models.ShareExport, token)
    if record is None:
        raise ApiError(Code.NOT_FOUND, "链接不存在或已删除", http_status=410)
    if record.expires_at <= now():
        raise ApiError(Code.NOT_FOUND, "链接已过期，请重新生成", http_status=410)
    if not storage.exists(record.file_path):
        raise ApiError(Code.NOT_FOUND, "摘要文件已不存在", http_status=410)
    return PlainTextResponse(
        storage.read_text(record.file_path), media_type="text/plain; charset=utf-8"
    )


def _render_summary(
    db: Session,
    patient: models.PatientProfile,
    payload: schemas.TimelineExport,
) -> tuple[str, int]:
    """模板化填空（PRD §14 #15 的降级路径）。默认近 90 天。

    第二个返回值是「摘要里实际有幾块内容」（区间内事件数 + 预问诊数），
    0 就说明没什么可导出的，调用方据此拒绝生成空链接。
    """
    end = payload.end or now()
    start = payload.start or (end - timedelta(days=90))
    events = list(
        db.scalars(
            select(models.TimelineEvent)
            .where(
                models.TimelineEvent.patient_id == patient.id,
                models.TimelineEvent.hidden.is_(False),
                models.TimelineEvent.occurred_at >= start,
                models.TimelineEvent.occurred_at <= end,
            )
            .order_by(models.TimelineEvent.occurred_at.desc())
        )
    )
    preconsults = list(
        db.scalars(
            select(models.PreConsult)
            .where(models.PreConsult.patient_id == patient.id, models.PreConsult.status == "done")
            .order_by(models.PreConsult.created_at.desc())
        )
    )

    lines = [
        "就诊摘要（明路生成）",
        f"就诊人：{patient.name}（{patient.relation}）",
        f"统计区间：{start.date()} ~ {end.date()}",
        "",
        "【主诉与病程】",
    ]
    if preconsults:
        stored = preconsults[0].structured_json or {}
        lines.append(stored.get("chief_complaint") or preconsults[0].chief_complaint)
        if stored.get("present_illness"):
            lines.append(stored["present_illness"])
    else:
        lines.append("（暂无预问诊记录）")

    lines += ["", "【既往史与过敏史】"]
    # 优先用预问诊里患者自述的既往史/过敏史/用药，档案里没有时才退回档案字段。
    # 少了这一段，医生拿到的摘要比患者实际说过的话少一截。
    latest = preconsults[0].structured_json if preconsults else {}
    chronic = "、".join(patient.chronic_conditions or []) or latest.get("past_history") or "无记录"
    lines.append(f"慢性病与既往史：{chronic}")
    lines.append(f"过敏史：{latest.get('allergy_history') or patient.allergy_notes or '无记录'}")
    medication = latest.get("medication_history")
    if medication:
        lines.append(f"当前用药：{medication}")

    lines += ["", "【近期健康记录】"]
    if events:
        lines += [
            f"- {e.occurred_at.date()} [{e.type}] {e.title}" + (f"：{e.summary}" if e.summary else "")
            for e in events[:20]
        ]
    else:
        lines.append("（区间内无记录）")

    lines += ["", EXPORT_FOOTER]
    return "\n".join(lines), len(events) + len(preconsults)
