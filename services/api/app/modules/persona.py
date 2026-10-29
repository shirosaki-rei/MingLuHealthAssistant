"""角色配置与审核。

PRD §4.1 的关键切分：人格随便玩，医学能力永远归产品。落到代码就是——
`check_persona_text` 只审身份词与资历表述，**不限制** species / catchphrase /
worldview / tones 能填什么（V3.0 的 role_label 白名单已取消）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile
from fastapi import File as FileField
from sqlalchemy import select

from .. import models, schemas
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, require_action
from ..errors import ApiError, Code, not_found
from ..modules.entitlement import check_persona_quota
from ..modules.safety import check_persona_text
from ..platform import storage
from ..config import get_settings

router = APIRouter(tags=["persona"])

# 预设角色的头像固定由服务端下发，用户上传的只落在自定义角色上
PRESET_AVATARS = {
    "安安": "/static/avatars/anan.jpg",
    "老陈": "/static/avatars/laochen.jpg",
    "小简": "/static/avatars/xiaojian.jpg",
    "小叶": "/static/avatars/xiaoye.jpg",
}


def persona_out(p: models.Persona) -> schemas.PersonaOut:
    """角色出参。预设头像按名字映射到打包图；自定义头像审核通过才给 URL。"""
    if p.is_preset:
        avatar = PRESET_AVATARS.get(p.name)
    elif p.avatar_path and p.review_status == "approved":
        avatar = storage.public_url(p.avatar_path)
    else:
        avatar = None
    return schemas.PersonaOut(
        id=p.id,
        name=p.name,
        species=p.species,
        self_ref=p.self_ref,
        catchphrase=p.catchphrase,
        worldview=p.worldview,
        tones=p.tones,
        is_preset=p.is_preset,
        review_status=p.review_status,  # type: ignore[arg-type]
        avatar_url=avatar,
        created_at=p.created_at,
    )


@router.get("/v1/personas", response_model=schemas.PersonaList)
def list_personas(
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("persona.list")),
) -> schemas.PersonaList:
    presets = list(
        db.scalars(
            select(models.Persona)
            .where(models.Persona.is_preset.is_(True))
            .order_by(models.Persona.created_at)
        )
    )
    mine = list(
        db.scalars(
            select(models.Persona)
            .where(models.Persona.owner_user_id == user.id)
            .order_by(models.Persona.created_at.desc())
        )
    )
    return schemas.PersonaList(
        presets=[persona_out(p) for p in presets],
        mine=[persona_out(p) for p in mine],
    )


@router.post("/v1/personas", status_code=201, response_model=schemas.PersonaOut)
def create_persona(
    payload: schemas.PersonaCreate,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("persona.create")),
) -> schemas.PersonaOut:
    # 顺序是刻意的：内容审核 → 额度。
    # 反过来会让免费用户永远看不到「名字里有身份词」的提示，等付费才被告知。
    problems = check_persona_text(payload.name, payload.self_ref, payload.worldview, payload.catchphrase)
    if problems:
        raise ApiError(Code.PERSONA_FORBIDDEN_WORD, problems[0], details={"all": problems})
    check_persona_quota(db, user)

    persona = models.Persona(
        id=new_id(),
        owner_user_id=user.id,
        is_preset=False,
        name=payload.name,
        species=payload.species,
        self_ref=payload.self_ref,
        catchphrase=payload.catchphrase,
        worldview=payload.worldview,
        tones=payload.tones,
        review_status="pending",  # 走审核队列；规则已过，模型复审后台做
        source="member",
        created_at=now(),
    )
    db.add(persona)
    db.flush()
    return persona_out(persona)


@router.post("/v1/personas/{persona_id}/avatar", response_model=schemas.PersonaOut)
async def upload_persona_avatar(
    persona_id: str,
    *,
    file: UploadFile = FileField(...),
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("persona.create")),
) -> schemas.PersonaOut:
    """自定义角色头像。和人设同一个审核对象，所以 action 也复用 persona.create。

    预设角色一律 403：它们的头像由服务端固定下发，用户改了就没有「官方形象」可言。
    """
    persona = db.get(models.Persona, persona_id)
    if persona is None or persona.owner_user_id != user.id or persona.is_preset:
        # 不说清是「不存在」还是「不是你的」：两者都回 403，别给枚举账号资产的口子
        raise not_found("角色")

    content_type = (file.content_type or "").lower()
    if content_type not in storage.ALLOWED_IMAGE_TYPES:
        raise ApiError(Code.UPLOAD_BAD_TYPE, "只支持 jpg / png / webp 图片")
    content = await file.read()
    if len(content) > get_settings().upload_max_bytes:
        raise ApiError(
            Code.UPLOAD_TOO_LARGE,
            f"图片不能超过 {get_settings().upload_max_bytes // 1024 // 1024}MB",
        )

    # 旧文件不删，和账号头像一个路子：别为几个孤儿文件加引用计数
    persona.avatar_path = storage.save_upload(
        content, f"{new_id()}.{storage.ALLOWED_IMAGE_TYPES[content_type]}", "avatars"
    )
    # 换图等于重新送审：不让用户用一张过审图打掩护，再换成另一张
    persona.review_status = "pending"
    db.flush()
    return persona_out(persona)
