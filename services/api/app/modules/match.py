"""科室映射、医生排序。

排序权重（PRD §7.1）：科室/亚专业匹配度 35%、患者评价分（≥20 条才计入）25%、
职称 15%、响应速度 15%、价格 10%。

降级路径（PRD §14 #8）：映射表查不到就走关键词匹配，再不行让用户自己选，
不硬猜。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Iterable

from .. import models, schemas
from ..deps import CurrentUser, DbDep, PageQuery, paginate, require_action
from ..errors import ApiError, Code, not_found

router = APIRouter(tags=["match"])

# PRD §7.1 兜底：置信度 < 0.6 时展示 Top3 让用户选，不硬猜
LOW_CONFIDENCE = 0.6

TITLE_RANK = {
    "主任医师": 1.0,
    "副主任医师": 0.85,
    "主治医师": 0.6,
    "医师": 0.4,
    "住院医师": 0.3,
}

# 首批开放咨询的 4 个科室（PRD §7.1），其余只推荐不开放
OPEN_DEPARTMENTS = {"cardiology", "neurology", "endocrinology", "dermatology"}


@router.post("/v1/match/departments", response_model=schemas.DepartmentMatchList)
def match_departments(
    payload: schemas.MatchRequest,
    db: DbDep,
    _: None = Depends(require_action("match.departments")),
) -> schemas.DepartmentMatchList:
    return schemas.DepartmentMatchList(items=match_departments_service(db, payload.symptoms))


def match_departments_service(db: Session, symptoms: list[str]) -> list[schemas.DepartmentMatch]:
    text = " ".join(symptoms)
    departments = {d.code: d for d in db.scalars(select(models.Department))}
    scores: dict[str, float] = {}

    for mapping in db.scalars(select(models.DepartmentSymptom)):
        if mapping.symptom and mapping.symptom in text:
            scores[mapping.department_code] = scores.get(mapping.department_code, 0.0) + mapping.weight

    # 科室名/描述直接命中也算（「心内科」「皮肤问题」这类说法）
    for code, dept in departments.items():
        if dept.name and dept.name in text:
            scores[code] = scores.get(code, 0.0) + 2.0

    if not scores:
        return []

    best = max(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return [
        schemas.DepartmentMatch(
            code=code,
            name=departments[code].name,
            confidence=round(min(0.99, score / best), 2),
            reason=_reason(departments[code], score, low=score / best < LOW_CONFIDENCE),
            consult_enabled=bool(departments[code].consult_enabled),
        )
        for code, score in ranked
        if code in departments
    ]


def _reason(dept: models.Department, score: float, low: bool) -> str:
    base = f"症状描述与{dept.name}的常见主诉吻合"
    if dept.description:
        base += f"，{dept.description}"
    if low:
        base += "；把握不大，建议你从下面几个里选，或直接找医生"
    if not dept.consult_enabled:
        base += "。该科室暂未开放线上咨询，可以先看知识或线下就诊"
    return base


@router.get("/v1/departments")
def list_departments(
    consult_only: bool = False,
    *,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("department.list")),
) -> dict:
    """症状匹配一个都没中时，用这个列表让用户自己选（PRD §7.1 兜底）。"""
    stmt = select(models.Department)
    if consult_only:
        stmt = stmt.where(models.Department.consult_enabled.is_(True))
    rows = list(db.scalars(stmt.order_by(models.Department.sort, models.Department.code)))
    return {
        "items": [
            {
                "code": d.code,
                "name": d.name,
                "description": d.description,
                "consult_enabled": d.consult_enabled,
            }
            for d in rows
        ]
    }


@router.get("/v1/doctors", response_model=schemas.DoctorList)
def list_doctors(
    db: DbDep,
    user: CurrentUser,
    department_code: str = Query(...),
    online_only: bool = Query(True),
    page: PageQuery = Depends(),
    _: None = Depends(require_action("doctor.list")),
) -> schemas.DoctorList:
    stmt = select(models.Doctor).where(models.Doctor.department_code == department_code)
    if online_only:
        stmt = stmt.where(models.Doctor.online_status == "online")
    doctors, total, has_more = paginate(db, stmt, page)
    ranked = sorted(doctors, key=lambda d: _doctor_score(d), reverse=True)
    names = _dept_names(db, [d.department_code for d in ranked])
    return schemas.DoctorList(
        items=[_doctor_out(d, names.get(d.department_code)) for d in ranked],
        total=total,
        has_more=has_more,
    )


@router.get("/v1/doctors/{doctor_id}", response_model=schemas.DoctorOut)
def read_doctor(
    doctor_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("doctor.detail")),
) -> schemas.DoctorOut:
    doctor = db.get(models.Doctor, doctor_id)
    if doctor is None:
        raise not_found("医生")
    names = _dept_names(db, [doctor.department_code])
    return _doctor_out(doctor, names.get(doctor.department_code))


def _doctor_score(doctor: models.Doctor) -> float:
    """PRD §7.1 的权重表。评价分不足 20 条不计入，避免 1 条好评排第一。"""
    rating = (doctor.rating / 5.0) if doctor.review_count >= 20 else 0.5
    title = TITLE_RANK.get(doctor.title, 0.4)
    speed = max(0.0, 1.0 - (doctor.avg_reply_hours / 48.0))
    price = max(0.0, 1.0 - (doctor.price_cents / 20000))
    online = {"online": 1.0, "busy": 0.5, "offline": 0.0}[doctor.online_status]
    return 0.35 * online + 0.25 * rating + 0.15 * title + 0.15 * speed + 0.10 * price


def _doctor_out(doctor: models.Doctor, dept_name: str | None = None) -> schemas.DoctorOut:
    return schemas.DoctorOut(
        id=doctor.id,
        name=doctor.name,
        department_code=doctor.department_code,
        department_name=dept_name,
        title=doctor.title,
        hospital=doctor.hospital,
        specialties=doctor.specialties or doctor.sub_specialty,
        online_status=doctor.online_status,  # type: ignore[arg-type]
        price_cents=doctor.price_cents,
        response_minutes=int(round(doctor.avg_reply_hours * 60)),
        match_reason=_match_reason(doctor),
    )


def _dept_names(db: Session, codes: Iterable[str]) -> dict[str, str]:
    """批量取科室中文名，避免每个医生单查一次。"""
    wanted = {c for c in codes if c}
    if not wanted:
        return {}
    rows = db.execute(
        select(models.Department.code, models.Department.name).where(models.Department.code.in_(wanted))
    ).all()
    return {code: name for code, name in rows}


def _match_reason(doctor: models.Doctor) -> str:
    specialties = "、".join((doctor.specialties or doctor.sub_specialty or [])[:2])
    bits = [f"{doctor.title}"]
    if specialties:
        bits.append(f"擅长{specialties}")
    bits.append(f"近 30 天平均 {doctor.avg_reply_hours:.1f} 小时回复")
    if doctor.review_count:
        bits.append(f"{doctor.review_count} 条评价 {doctor.rating:.1f} 分")
    return "；".join(bits)


def get_doctor_or_404(db: Session, doctor_id: str) -> models.Doctor:
    """订单模块用：未核验资质的医生不能接单（PRD §13.6）。"""
    doctor = db.get(models.Doctor, doctor_id)
    if doctor is None:
        raise not_found("医生")
    if not doctor.license_verified:
        raise ApiError(Code.DOCTOR_UNAVAILABLE, "这位医生还在资质核验中，暂时不能预约")
    if doctor.online_status == "offline":
        raise ApiError(Code.DOCTOR_UNAVAILABLE, "这位医生当前离线，可以换个时间或看看其他医生")
    return doctor
