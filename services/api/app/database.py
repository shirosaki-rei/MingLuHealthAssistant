"""连接池、会话、Base，以及一个跨库可用的时间列类型。

时间列用 IsoDateTime 而不是 DateTime(timezone=True)：SQLite 读回来会丢时区，
而契约第一条就是「ISO 8601 带时区」。存 ISO 字符串可以做到字节级往返，
SQLite / PostgreSQL 行为一致。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import String, Text, TypeDecorator, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import JSON

from .config import get_settings

# 产品面向境内用户，对外一律 +08:00（契约示例即 2026-10-22T09:00:00+08:00）
TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    """统一到秒。契约示例是 2026-10-22T09:00:00+08:00，微秒对健康数据没有意义；
    在源头截断，写库和直接返回对象的路径才一致（刚 flush 的对象不会走 DB 往返）。"""
    return datetime.now(TZ).replace(microsecond=0)


def new_id() -> str:
    return str(uuid.uuid4())


class IsoDateTime(TypeDecorator):
    """存 ISO 8601 带时区字符串，读回来是 aware datetime。"""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=TZ)
        return value.astimezone(TZ).isoformat()

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        # 统一到秒：契约示例是 2026-10-22T09:00:00+08:00。微秒对健康数据没有意义，
        # 去掉后前端拿到的时间戳格式一致，排序在 SQL 里做，不受影响。
        return parsed.replace(microsecond=0)


class Base(DeclarativeBase):
    pass


def utcnow_iso() -> str:  # 仅 ai_log 之类的旁路字段用
    return datetime.now(timezone.utc).isoformat()


@lru_cache
def get_engine():
    settings = get_settings()
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # 内存库默认每个连接一份独立数据。StaticPool 让所有连接共用一个内存库，
        # 否则测试里 create_all 之后新连接查不到表。
        if settings.database_url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
    return create_engine(settings.database_url, **kwargs)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖。"""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "Base",
    "JSON",
    "IsoDateTime",
    "String",
    "Text",
    "TZ",
    "date",
    "get_db",
    "get_engine",
    "get_sessionmaker",
    "new_id",
    "now",
    "utcnow_iso",
]
