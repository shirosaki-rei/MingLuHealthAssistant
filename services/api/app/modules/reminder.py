"""提醒 CRUD 与调度。

架构 §5.3：服务端只负责「算出该什么时候响」，客户端负责「真的响」。
- 服务端：reminder 表 + fire_at，客户端从 /v1/reminders/pending 拉取后本地注册精确闹钟
- 为什么不服务端直接推：推送到达率与准时性都不可控，而用药提醒要求分钟级精度
- 开机恢复失效时的兜底：只要用户打开过 App，未过期的提醒就能恢复

频控（PRD §6）：主动关心每日 ≤1 次；用户设定的用药/测量提醒每日 ≤5 条；
夜间 23:00–07:00 静默（危急值除外）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import TZ, new_id, now
from ..deps import CurrentUser, DbDep, require_action
from ..errors import ApiError, Code, not_found
from ..modules.profile import get_owned_patient

router = APIRouter(tags=["reminder"])

NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7
MAX_USER_REMINDERS_PER_DAY = 5  # PRD §6：用药/测量提醒每日 ≤5 条


@router.post("/v1/reminders", status_code=201, response_model=schemas.ReminderOut)
def create_reminder(
    payload: schemas.ReminderCreate,
    *,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("reminder.create")),
) -> schemas.ReminderOut:
    patient = get_owned_patient(db, user.id, payload.patient_id)

    fire_at = payload.fire_at if payload.fire_at.tzinfo else payload.fire_at.replace(tzinfo=TZ)
    if fire_at <= now():
        raise ApiError(Code.VALIDATION_ERROR, "触发时间需要晚于当前时间", details={"field": "fire_at"})

    _check_night_quiet(fire_at, payload.kind)
    _check_daily_cap(db, patient.id, payload.kind)

    reminder = models.Reminder(
        id=new_id(),
        user_id=user.id,
        patient_id=patient.id,
        kind=payload.kind,
        title=payload.title,
        content=payload.content,
        fire_at=fire_at,
        recurrence=payload.recurrence or "once",
        status="pending",
        created_at=now(),
    )
    db.add(reminder)
    db.flush()
    return schemas.ReminderOut.model_validate(reminder)


def _check_night_quiet(fire_at: datetime, kind: str) -> None:
    """夜间静默 23:00–07:00。危急值指导除外。"""
    if kind == "followup":  # 复诊提醒带医生结论，不属于打扰
        return
    hour = fire_at.astimezone(TZ).hour
    if hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR:
        raise ApiError(
            Code.VALIDATION_ERROR,
            f"{NIGHT_START_HOUR}:00 到 {NIGHT_END_HOUR}:00 是静默时段，换个时间吧",
            details={"field": "fire_at", "quiet_hours": [NIGHT_START_HOUR, NIGHT_END_HOUR]},
        )


def _check_daily_cap(db: Session, patient_id: str, kind: str) -> None:
    if kind not in {"medication", "measurement"}:
        return
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    same_day = (
        db.query(models.Reminder)
        .filter(
            models.Reminder.patient_id == patient_id,
            models.Reminder.kind.in_(("medication", "measurement")),
            models.Reminder.fire_at >= day_start,
            models.Reminder.status != "cancelled",
        )
        .count()
    )
    if same_day >= MAX_USER_REMINDERS_PER_DAY:
        raise ApiError(
            Code.REMINDER_LIMIT_EXCEEDED,
            f"同一天最多 {MAX_USER_REMINDERS_PER_DAY} 条用药/测量提醒",
            details={"limit": MAX_USER_REMINDERS_PER_DAY},
        )


@router.get("/v1/reminders/pending", response_model=schemas.PendingReminderList)
def pending_reminders(
    db: DbDep,
    user: CurrentUser,
    within_hours: int = Query(24, ge=1, le=72),
    _: None = Depends(require_action("reminder.pending")),
) -> schemas.PendingReminderList:
    """客户端在启动时与每次进入提醒页时调用，用于全量重排本地闹钟。"""
    horizon = now() + timedelta(hours=within_hours)
    items = list(
        db.scalars(
            select(models.Reminder)
            .where(
                models.Reminder.user_id == user.id,
                models.Reminder.status == "pending",
                models.Reminder.fire_at <= horizon,
            )
            .order_by(models.Reminder.fire_at)
        )
    )
    return schemas.PendingReminderList(
        items=[
            schemas.PendingReminder(
                id=r.id, fire_at=r.fire_at, title=r.title, content=r.content
            )
            for r in items
        ]
    )


@router.delete("/v1/reminders/{reminder_id}", status_code=204)
def delete_reminder(
    reminder_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("reminder.delete")),
) -> None:
    reminder = db.get(models.Reminder, reminder_id)
    if reminder is None or reminder.user_id != user.id:
        raise not_found("提醒")
    # 置 cancelled 而不是物理删除：客户端重排时需要能区分「已取消」与「从没设过」
    reminder.status = "cancelled"
    db.flush()


def advance_fired(db: Session) -> int:
    """定时任务入口：把到点的 pending 提醒推进。

    once → fired；带循环的按 recurrence 算下一次 fire_at 后仍是 pending。
    """
    stamp = now()
    due = list(
        db.query(models.Reminder)
        .filter(models.Reminder.status == "pending", models.Reminder.fire_at <= stamp)
        .all()
    )
    for reminder in due:
        nxt = _next_occurrence(reminder.recurrence, reminder.fire_at)
        if nxt is None or nxt <= stamp:
            reminder.status = "fired"
        else:
            reminder.fire_at = nxt
    return len(due)


def _next_occurrence(recurrence: str, fire_at: datetime) -> datetime | None:
    """支持 daily / weekly:mon,wed / once。RRULE 全量语法留给二期。"""
    rule = (recurrence or "once").strip().lower()
    if rule.startswith("daily"):
        return fire_at + timedelta(days=1)
    if rule.startswith("weekly"):
        days = [d.strip()[:3] for d in rule.split(":", 1)[1].split(",")] if ":" in rule else []
        weekday = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        targets = sorted(weekday[d] for d in days if d in weekday)
        if not targets:
            return fire_at + timedelta(days=7)
        current = fire_at.weekday()
        for offset in range(1, 8):
            if (current + offset) % 7 in targets:
                return fire_at + timedelta(days=offset)
    return None
