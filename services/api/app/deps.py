"""共享依赖：鉴权、分页、就医路径 action 声明。

架构 §4.4 的「路由装饰器」落地方式：

- 每个对外接口**必须**用 `Depends(require_action("xxx"))` 显式声明 action
- 声明的 action 若不在就医路径白名单里，就挂着一串付费/限额检查
- 启动时 main.py 自检所有路由：未声明 action、或 action 拼错 → 进程起不来

这样「漏配一个路由」和「action 拼错导致付费墙被绕过」都是启动即失败，不是线上事故。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Annotated, TypeVar

import jwt
from fastapi import Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .database import get_db
from .errors import unauthorized

log = logging.getLogger("minglu")

# action → 该动作要跑的前置检查。就医路径动作**不注册在这里**，也就没有任何检查。
_ACTION_CHECKS: dict[str, list[Callable[[Session, models.User], None]]] = {}


def register_checks(action: str, *checks: Callable[[Session, models.User], None]) -> None:
    """给非就医路径动作挂检查（额度 / 频控）。由各模块在 import 时调用。"""
    _ACTION_CHECKS.setdefault(action, []).extend(checks)


# 不需要登录的端点。/v1/auth/* 在拿到 token 之前，/v1/exports/* 靠 URL 里的 token 鉴权。
# 这些动作既不免费也不挂检查，列在这里只是为了通过启动自检（拼错会被拦住）。
PUBLIC_ACTIONS = frozenset({"auth.sms", "auth.login", "export.read"})


def known_action(action: str) -> bool:
    """供启动自检用：就医路径动作、注册了检查的动作、或显式公开的动作才算已知。"""
    from .modules.entitlement import MEDICAL_ACTIONS

    return action in MEDICAL_ACTIONS or action in _ACTION_CHECKS or action in PUBLIC_ACTIONS


def require_action(action: str) -> Callable[[Session, models.User], None]:
    """路由依赖。就医路径动作是空操作；其余按注册顺序跑检查。"""
    checks = tuple(_ACTION_CHECKS.get(action, ()))

    async def _dep(
        db: Annotated[Session, Depends(get_db)],
        user: Annotated[models.User, Depends(current_user)],
    ) -> None:
        for check in checks:
            check(db, user)

    _dep.__minglu_action__ = action  # type: ignore[attr-defined]
    _dep.__name__ = f"action[{action}]"
    return _dep


def require_public_action(action: str) -> Callable[[], None]:
    """未登录端点的 action 声明。本身不做事，存在意义是让漏配/拼错在启动时暴露。"""

    async def _dep() -> None:
        return None

    _dep.__minglu_action__ = action  # type: ignore[attr-defined]
    _dep.__name__ = f"public_action[{action}]"
    return _dep


DbDep = Annotated[Session, Depends(get_db)]


def current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),
) -> models.User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("请先登录")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise unauthorized("登录状态已失效，请重新登录") from None
    user = db.get(models.User, str(payload.get("sub", "")))
    if user is None or user.status != "active":
        raise unauthorized("账号不可用，请重新登录")
    return user


CurrentUser = Annotated[models.User, Depends(current_user)]


class PageQuery:
    """page / page_size 查询参数。契约：page 从 1 开始，page_size 默认 20、上限 100。"""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


T = TypeVar("T")


def paginate(db: Session, stmt, page: PageQuery) -> tuple[Sequence[T], int, bool]:
    """返回 (items, total, has_more)。"""
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = list(db.scalars(stmt.offset(page.offset).limit(page.page_size)))
    return items, total, page.offset + len(items) < total


__all__ = [
    "PUBLIC_ACTIONS",
    "CurrentUser",
    "DbDep",
    "PageQuery",
    "current_user",
    "known_action",
    "paginate",
    "register_checks",
    "require_action",
    "require_public_action",
]
