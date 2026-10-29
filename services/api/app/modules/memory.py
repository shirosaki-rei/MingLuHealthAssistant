"""智能体记忆：查看、修改、删除。

PRD §4.5：记忆以结构化 KV 存储，每条标注来源对话与时间，**多端均可查看与删除**
（小程序也必须支持，否则违反个人信息可携带与删除要求）。
记忆绑定就诊人，不绑定角色；删除任意一条，所有角色同时失效。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, PageQuery, paginate, require_action
from ..errors import not_found
from ..modules.profile import get_owned_patient

router = APIRouter(tags=["memory"])


@router.get("/v1/memories", response_model=schemas.MemoryList)
def list_memories(
    db: DbDep,
    user: CurrentUser,
    patient_id: str,
    page: PageQuery = Depends(),
    _: None = Depends(require_action("memory.read")),
) -> schemas.MemoryList:
    get_owned_patient(db, user.id, patient_id)
    stmt = (
        select(models.AgentMemory)
        .where(models.AgentMemory.user_id == user.id, models.AgentMemory.patient_id == patient_id)
        .order_by(models.AgentMemory.created_at.desc())
    )
    items, total, has_more = paginate(db, stmt, page)
    return schemas.MemoryList(
        items=[schemas.AgentMemoryOut.model_validate(m) for m in items],
        total=total,
        has_more=has_more,
    )


@router.patch("/v1/memories/{memory_id}", response_model=schemas.AgentMemoryOut)
def update_memory(
    memory_id: str,
    payload: schemas.MemoryUpdate,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("memory.update")),
) -> models.AgentMemory:
    memory = _owned(db, user.id, memory_id)
    if payload.key is not None:
        memory.key = payload.key
    if payload.value is not None:
        memory.value = payload.value
    # 用户改过就算确认过：不再向用户追问「我记一下……对吗」
    memory.confirmed_at = now()
    db.flush()
    return memory


@router.delete("/v1/memories/{memory_id}", status_code=204)
def delete_memory(
    memory_id: str,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("memory.delete")),
) -> None:
    """真删除，所有角色同时失效该条（PRD §4.3）。"""
    db.delete(_owned(db, user.id, memory_id))
    db.flush()


def _owned(db: Session, user_id: str, memory_id: str) -> models.AgentMemory:
    memory = db.get(models.AgentMemory, memory_id)
    if memory is None or memory.user_id != user_id:
        raise not_found("记忆")
    return memory


def remember(
    db: Session,
    *,
    user_id: str,
    patient_id: str,
    kind: str,
    key: str,
    value: str,
    source_msg_id: str | None = None,
    confirmed: bool = False,
) -> models.AgentMemory:
    """对话模块写入记忆用。同 key 已存在则更新，不堆重复条目。"""
    existing = db.scalar(
        select(models.AgentMemory).where(
            models.AgentMemory.user_id == user_id,
            models.AgentMemory.patient_id == patient_id,
            models.AgentMemory.key == key,
        )
    )
    if existing is not None:
        existing.value = value
        if confirmed:
            existing.confirmed_at = now()
        return existing
    memory = models.AgentMemory(
        id=new_id(),
        user_id=user_id,
        patient_id=patient_id,
        type=kind,
        key=key,
        value=value,
        source_msg_id=source_msg_id,
        confirmed_at=now() if confirmed else None,
        created_at=now(),
    )
    db.add(memory)
    return memory


def recent_memories(db: Session, user_id: str, patient_id: str, limit: int = 10) -> list[models.AgentMemory]:
    """L3 上下文注入：事实型优先，按相关度（确认状态 + 新鲜度）排序。"""
    rows = list(
        db.scalars(
            select(models.AgentMemory)
            .where(
                models.AgentMemory.user_id == user_id,
                models.AgentMemory.patient_id == patient_id,
            )
            .order_by(models.AgentMemory.created_at.desc())
            .limit(limit * 3)
        )
    )
    rows.sort(key=lambda m: (m.type != "fact", m.confirmed_at is None, m.created_at), reverse=False)
    return rows[:limit]


def count_memories(db: Session, user_id: str, patient_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(models.AgentMemory.id)).where(
                models.AgentMemory.user_id == user_id,
                models.AgentMemory.patient_id == patient_id,
            )
        )
        or 0
    )
