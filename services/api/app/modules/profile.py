"""就诊人档案（多档案）。

PRD §10 的权限模型写死在这里：全部档案归属创建者账号，仅创建者可见，
不与其他账号共享。首期不做多人共享。

条数按会员分：会员 6 位（本人 + 5 位家人），免费 3 位（本人 + 2 位家人）。
多建档本身仍是就医路径，永远放行，只是条数有别。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from .. import models, schemas
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, require_action
from ..errors import ApiError, Code, not_found
from ..modules.entitlement import entitlements_of, get_membership

router = APIRouter(tags=["patient"])


def get_owned_patient(db, user_id: str, patient_id: str) -> models.PatientProfile:
    """跨模块调用的唯一入口。拿不到就 404，不区分「不存在」与「不属于你」。"""
    patient = db.get(models.PatientProfile, patient_id)
    if patient is None or patient.user_id != user_id:
        raise not_found("就诊人")
    return patient


@router.get("/v1/patients", response_model=schemas.PatientList)
def list_patients(
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("patient.list")),
) -> schemas.PatientList:
    items = list(
        db.scalars(
            select(models.PatientProfile)
            .where(models.PatientProfile.user_id == user.id)
            .order_by(models.PatientProfile.is_default.desc(), models.PatientProfile.created_at)
        )
    )
    return schemas.PatientList(items=[schemas.PatientOut.model_validate(p) for p in items])


@router.post("/v1/patients", status_code=201, response_model=schemas.PatientOut)
def create_patient(
    payload: schemas.PatientCreate,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("patient.create")),
) -> models.PatientProfile:
    existing = (
        db.query(models.PatientProfile).filter(models.PatientProfile.user_id == user.id).count()
    )
    limit = entitlements_of(get_membership(db, user.id))["max_patients"]
    if existing >= limit:
        # 契约测试 #9：满额返回明确业务码，不是 500
        raise ApiError(
            Code.PATIENT_LIMIT_EXCEEDED,
            f"最多添加 {limit} 位就诊人。会员可以到 6 位。",
            details={"limit": limit},
        )

    # 未成年人必须监护人同意（PRD §13.7），写 consent_log 供 PIPL 举证
    is_minor = payload.birth_date is not None and (now().date() - payload.birth_date).days < 18 * 365
    if is_minor and not payload.guardian_consent:
        raise ApiError(
            Code.VALIDATION_ERROR,
            "未成年人档案需要监护人同意",
            details={"field": "guardian_consent"},
        )

    patient = models.PatientProfile(
        id=new_id(),
        user_id=user.id,
        name=payload.name,
        relation=payload.relation,
        gender=payload.gender,
        birth_date=payload.birth_date.isoformat() if payload.birth_date else None,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        chronic_conditions=payload.chronic_conditions,
        allergy_notes=payload.allergy_notes,
        is_default=existing == 0,
        created_at=now(),
    )
    db.add(patient)
    if is_minor:
        db.add(
            models.ConsentLog(
                user_id=user.id,
                type="guardian",
                version="1.0",
                scene="patient.create",
                agreed_at=now(),
            )
        )
    db.flush()
    return patient


@router.get("/v1/patients/{patient_id}", response_model=schemas.PatientOut)
def read_patient(
    patient_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("patient.read")),
) -> models.PatientProfile:
    return get_owned_patient(db, user.id, patient_id)


@router.patch("/v1/patients/{patient_id}", response_model=schemas.PatientOut)
def update_patient(
    patient_id: str,
    payload: schemas.PatientUpdate,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("patient.update")),
) -> models.PatientProfile:
    patient = get_owned_patient(db, user.id, patient_id)
    for field in ("name", "height_cm", "weight_kg", "chronic_conditions", "allergy_notes"):
        value = getattr(payload, field)
        if value is not None:
            setattr(patient, field, value)
    db.flush()
    return patient


@router.delete("/v1/patients/{patient_id}", status_code=204)
def delete_patient(
    patient_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("patient.delete")),
) -> None:
    """删除档案及其健康记录、时间线事件；咨询订单保留（交易凭证，PRD §10）。"""
    patient = get_owned_patient(db, user.id, patient_id)
    db.query(models.TimelineEvent).filter(models.TimelineEvent.patient_id == patient.id).delete()
    db.query(models.HealthRecord).filter(models.HealthRecord.patient_id == patient.id).delete()
    db.query(models.AgentMemory).filter(models.AgentMemory.patient_id == patient.id).delete()
    db.delete(patient)
    db.flush()
