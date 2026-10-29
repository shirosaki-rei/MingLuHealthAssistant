"""登录、会话、设备。

验证码存在 sms_code 表里（架构说用 Redis 做频控与会话，本地开发不引 Redis，
换 DB 表，接口不变）。token 是 HS256 JWT，sub = user.id。
"""
from __future__ import annotations

import logging
import random
from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select

from .. import models, schemas
from ..config import get_settings
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, require_action, require_public_action
from ..errors import ApiError, Code, request_id_var
from ..modules.entitlement import rate_limit_sms
from ..platform import storage

log = logging.getLogger("minglu")
router = APIRouter(tags=["auth"])

SMS_TTL_SECONDS = 300
MAX_CODE_ATTEMPTS = 5


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}"


def user_out(user: models.User) -> schemas.UserOut:
    """账号出参。头像 URL 现算不落库——换 PUBLIC_BASE_URL 不用回填历史数据。"""
    return schemas.UserOut(
        id=user.id,
        phone_masked=user.phone_masked,
        nickname=user.nickname,
        avatar_url=storage.public_url(user.avatar_path) if user.avatar_path else None,
        created_at=user.created_at,
    )


@router.post("/v1/auth/sms", response_model=schemas.SmsResult)
def send_sms(
    payload: schemas.SmsRequest,
    db: DbDep,
    _: None = Depends(require_public_action("auth.sms")),
) -> schemas.SmsResult:
    rate_limit_sms(payload.phone)
    code = f"{random.randint(0, 999999):06d}"
    stamp = now()
    record = db.get(models.SmsCode, payload.phone)
    if record is None:
        record = models.SmsCode(phone=payload.phone, code=code, expires_at=stamp + timedelta(seconds=SMS_TTL_SECONDS), last_sent_at=stamp)
        db.add(record)
    else:
        record.code = code
        record.expires_at = stamp + timedelta(seconds=SMS_TTL_SECONDS)
        record.last_sent_at = stamp
        record.attempts = 0
    db.flush()

    # ponytail: 没有接短信通道，dev 下把验证码写进日志方便本地联调。
    # 生产必须 SMS_ECHO=false，且这条日志本身就该去掉。
    if get_settings().sms_echo:
        log.warning("[dev] 验证码 phone=%s code=%s", payload.phone, code)
    return schemas.SmsResult(request_id=request_id_var.get(), expires_in=SMS_TTL_SECONDS)


@router.post("/v1/auth/login", response_model=schemas.LoginResult)
def login(
    payload: schemas.LoginRequest,
    db: DbDep,
    _: None = Depends(require_public_action("auth.login")),
) -> schemas.LoginResult:
    record = db.get(models.SmsCode, payload.phone)
    if record is None:
        raise ApiError(Code.SMS_CODE_INVALID, "请先获取验证码")
    if record.expires_at <= now():
        raise ApiError(Code.SMS_CODE_EXPIRED, "验证码已过期，请重新获取")
    if record.attempts >= MAX_CODE_ATTEMPTS:
        raise ApiError(Code.RATE_LIMITED, "验证码错误次数过多，请重新获取", http_status=429)
    if record.code != payload.code:
        record.attempts += 1
        raise ApiError(Code.SMS_CODE_INVALID, "验证码不正确")

    db.delete(record)  # 一次性
    user = db.scalar(select(models.User).where(models.User.phone == payload.phone))
    if user is None:
        user = models.User(
            id=new_id(),
            phone=payload.phone,
            phone_masked=mask_phone(payload.phone),
            created_at=now(),
        )
        db.add(user)
        db.flush()
    if user.status != "active":
        raise ApiError(Code.FORBIDDEN, "账号已被停用，请联系客服")

    settings = get_settings()
    token = jwt.encode(
        {"sub": user.id, "phone": payload.phone},
        settings.jwt_secret,
        algorithm="HS256",
    )
    return schemas.LoginResult(
        token=token,
        expires_in=settings.token_ttl,
        user=user_out(user),
    )


@router.patch("/v1/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.MeUpdate,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("me.update")),
) -> schemas.UserOut:
    """改昵称。手机号是登录凭据，不走这里——换号等于换账号。"""
    user.nickname = payload.nickname.strip()
    db.flush()
    return user_out(user)


@router.post("/v1/me/avatar", response_model=schemas.UserOut)
async def upload_avatar(
    *,
    file: UploadFile = File(...),
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("me.update")),
) -> schemas.UserOut:
    """换头像。和 /v1/reports 同一套校验与落盘，只换了子目录。

    旧文件不删：同一账号反复上传会留下几个孤儿文件，本地开发无所谓；
    真要清理时按 avatars/ 前缀扫一遍即可，别为它加引用计数。
    """
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
        content, f"{new_id()}.{storage.ALLOWED_IMAGE_TYPES[content_type]}", "avatars"
    )
    user.avatar_path = relpath
    db.flush()
    return user_out(user)
