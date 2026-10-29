"""预问诊状态机。

架构 §5.2：
  start → asking(round=0)
    → 每轮 answer 写入 structured_json，round++
    → round ≥ 6 或用户喊停 → 强制 finish（标注「信息较少」）
    → 任意一轮命中红旗 → 中断，status=aborted_redflag
    → finish：生成病历草稿，status=confirming
    → confirm：status=done，写 timeline_event

追问用固定问题序列（PRD §14 #6 的降级路径：模板固定问题序列）。
单轮 ≤2 个问题，避免像问卷。中途退出后再次进入读 round 与已答字段续接。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, require_action
from ..errors import ApiError, Code, not_found
from ..modules.match import match_departments_service
from ..modules.memory import remember
from ..modules.profile import get_owned_patient
from ..modules.safety import detect_redflag

router = APIRouter(tags=["preconsult"])

MAX_ROUNDS = 6

# 固定追问序列。id 稳定，客户端可据此缓存选项；单轮最多 2 问。
QUESTION_BANK: list[list[schemas.Question]] = [
    [
        schemas.Question(id="onset", text="这个情况什么时候开始的？持续多久了？", type="text"),
        schemas.Question(id="severity", text="现在有多难受（1~10 分，10 分最难忍）？", type="number"),
    ],
    [
        schemas.Question(
            id="aggravate",
            text="什么情况下会加重或缓解？",
            type="multiple",
            options=["活动/用力时加重", "体位改变时加重", "休息后缓解", "吃药后缓解", "没什么规律"],
        ),
    ],
    [
        schemas.Question(
            id="accompany",
            text="有没有伴随症状？",
            type="multiple",
            options=["恶心呕吐", "头晕", "心慌", "出汗", "视物模糊", "耳鸣", "失眠", "都没有"],
        ),
    ],
    [
        schemas.Question(id="past_history", text="既往有什么慢性病或做过什么手术吗？", type="text"),
        schemas.Question(id="medication", text="最近在吃什么药（含保健品）？", type="text"),
    ],
    [
        schemas.Question(
            id="checked",
            text="最近测过相关指标或做过检查吗？",
            type="single",
            options=["测过，数值有异常", "测过，正常", "没测过"],
        ),
    ],
    [
        schemas.Question(id="goal", text="你最想解决的是什么？", type="text"),
    ],
]


def _questions_for(round_index: int) -> list[schemas.Question]:
    if round_index < len(QUESTION_BANK):
        return QUESTION_BANK[round_index]
    return []


def _to_out(session: models.PreConsult) -> schemas.PreConsultOut:
    answers = dict(session.structured_json or {})
    return schemas.PreConsultOut(
        id=session.id,
        status=session.status,  # type: ignore[arg-type]
        round=session.round,
        questions=_questions_for(session.round) if session.status == "asking" else [],
        answers=answers,
        redflag_level=session.redflag_level,  # type: ignore[arg-type]
        insufficient=session.insufficient,
    )


def get_owned_session(db: Session, user_id: str, session_id: str) -> models.PreConsult:
    session = db.get(models.PreConsult, session_id)
    if session is None or session.user_id != user_id:
        raise not_found("预问诊会话")
    return session


@router.post("/v1/preconsult/start", status_code=201, response_model=schemas.PreConsultOut)
def start(
    payload: schemas.PreConsultStart,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("preconsult.start")),
) -> schemas.PreConsultOut:
    patient = get_owned_patient(db, user.id, payload.patient_id)

    # 红旗在意图识别之前（架构 §5.1）：主诉本身就可能命急症
    hit = detect_redflag(payload.chief_complaint)
    if hit:
        session = models.PreConsult(
            id=new_id(),
            user_id=user.id,
            patient_id=patient.id,
            chief_complaint=payload.chief_complaint,
            structured_json={},
            round=0,
            status="aborted_redflag",
            redflag_level=hit.level,
            created_at=now(),
        )
        db.add(session)
        db.flush()
        return _to_out(session)

    session = models.PreConsult(
        id=new_id(),
        user_id=user.id,
        patient_id=patient.id,
        chief_complaint=payload.chief_complaint,
        structured_json={},
        round=0,
        status="asking",
        created_at=now(),
    )
    db.add(session)
    db.flush()
    return _to_out(session)


@router.post("/v1/preconsult/{session_id}/answer", response_model=schemas.PreConsultOut)
def answer(
    session_id: str,
    payload: schemas.PreConsultAnswer,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("preconsult.answer")),
) -> schemas.PreConsultOut:
    session = get_owned_session(db, user.id, session_id)
    if session.status != "asking":
        raise ApiError(
            Code.PRECONSULT_NOT_ACTIVE,
            "这次预问诊已经结束了",
            details={"status": session.status},
        )

    answer_text = "、".join(payload.answer) if isinstance(payload.answer, list) else str(payload.answer)

    # 每一轮都过红旗（PRD 验收：追问过程中红旗仍 100% 拦截）
    hit = detect_redflag(answer_text)
    if hit:
        session.status = "aborted_redflag"
        session.redflag_level = hit.level
        session.structured_json = {**(session.structured_json or {}), payload.question_id: payload.answer}
        db.flush()
        return _to_out(session)

    session.structured_json = {**(session.structured_json or {}), payload.question_id: payload.answer}
    session.round += 1

    if session.round >= MAX_ROUNDS:
        # 轮次上限：超出用现有信息直接生成，标注「信息较少」
        session.insufficient = True
    db.flush()
    return _to_out(session)


@router.post("/v1/preconsult/{session_id}/finish", response_model=schemas.MedicalRecordOut)
def finish(
    session_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("preconsult.finish")),
) -> schemas.MedicalRecordOut:
    session = get_owned_session(db, user.id, session_id)
    if session.status == "done":
        raise ApiError(Code.PRECONSULT_ALREADY_DONE, "病历已经确认过了")
    if session.status == "aborted_redflag":
        raise ApiError(Code.PRECONSULT_NOT_ACTIVE, "这次预问诊因红旗已中断，请直接就医")

    record = _build_record(db, session)
    session.status = "confirming"
    db.flush()
    return record


@router.post("/v1/preconsult/{session_id}/confirm", response_model=schemas.PreConsultOut)
def confirm(
    session_id: str,
    payload: schemas.PreConsultConfirm,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("preconsult.confirm")),
) -> schemas.PreConsultOut:
    """用户确认后 status → done，并写一条时间线事件。幂等。"""
    session = get_owned_session(db, user.id, session_id)
    if session.status == "done":
        return _to_out(session)
    if session.status != "confirming":
        raise ApiError(Code.PRECONSULT_NOT_ACTIVE, "请先调用 finish 生成病历", details={"status": session.status})

    record = _build_record(db, session)
    stored = dict(session.structured_json or {})
    stored.update(
        {
            "chief_complaint": payload.chief_complaint or record.chief_complaint,
            "present_illness": payload.present_illness or record.present_illness,
            "past_history": payload.past_history or record.past_history,
            "allergy_history": payload.allergy_history or record.allergy_history,
            "medication_history": payload.medication_history or record.medication_history,
        }
    )
    session.structured_json = stored
    session.status = "done"

    # 患者自述的既往史/用药是持久事实，落成事实型记忆（PRD §4.5 事实型：慢病、长期用药）。
    # 只认明确说出来的，不猜；用户之后可在「我的 → 智能体记忆」里改或删。
    for key, field in (("chronic", "past_history"), ("medication", "medication_history")):
        value = stored.get(field)
        if value:
            remember(
                db,
                user_id=user.id,
                patient_id=session.patient_id,
                kind="fact",
                key=key,
                value=str(value)[:200],
                confirmed=True,  # 病历已经用户确认过
            )

    db.add(
        models.TimelineEvent(
            patient_id=session.patient_id,
            user_id=user.id,
            type="preconsult",
            ref_id=session.id,
            title=f"预问诊病历：{record.chief_complaint}",
            summary=record.present_illness,
            occurred_at=now(),
        )
    )
    db.flush()
    return _to_out(session)


def _build_record(db: Session, session: models.PreConsult) -> schemas.MedicalRecordOut:
    """模板拼接（PRD §14 #7 的降级路径），字段完整率目标 ≥85%。"""
    answers = dict(session.structured_json or {})
    patient = db.get(models.PatientProfile, session.patient_id)

    present_illness = "；".join(
        f"{label}：{_text(answers.get(key))}"
        for key, label in (
            ("onset", "起病"),
            ("severity", "程度"),
            ("aggravate", "加重/缓解"),
            ("accompany", "伴随症状"),
            ("checked", "检查情况"),
        )
        if _text(answers.get(key))
    )

    past = _text(answers.get("past_history")) or (
        "、".join(patient.chronic_conditions or []) if patient and patient.chronic_conditions else ""
    )
    allergy = _text(answers.get("allergy")) or (
        patient.allergy_notes if patient and patient.allergy_notes else ""
    )
    medication = _text(answers.get("medication"))

    departments = [
        item.code
        for item in match_departments_service(
            db, [session.chief_complaint, *(v for v in answers.values() if isinstance(v, str))]
        )
    ]

    return schemas.MedicalRecordOut(
        session_id=session.id,
        chief_complaint=session.chief_complaint,
        present_illness=present_illness or "信息较少",
        past_history=past or None,
        allergy_history=allergy or None,
        medication_history=medication or None,
        suggested_departments=departments or None,
        ready_for_confirm=True,
    )


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    return str(value).strip()


__all__ = ["MAX_ROUNDS", "QUESTION_BANK", "get_owned_session", "router"]
