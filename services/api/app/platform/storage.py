"""平台层：对象存储（报告图片、导出的就诊摘要）。

生产换私有桶 + 签名 URL；本地开发落盘即可。所有路径都在 storage_dir 下面，
不接受外部传入的绝对路径。
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import get_settings

_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")

ALLOWED_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _root() -> Path:
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_name(raw: str) -> str:
    """去掉路径分隔与异常字符，防止 ../../../ 穿越。"""
    return _UNSAFE.sub("_", Path(raw or "file").name)[:80] or "file"


def save_upload(content: bytes, filename: str, subdir: str) -> str:
    """写入存储，返回相对路径（不含根目录）。"""
    target = _root() / safe_name(subdir) / safe_name(filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target.relative_to(_root()).as_posix()


def write_text(relpath: str, text: str) -> str:
    target = _root() / safe_name(relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target.relative_to(_root()).as_posix()


def read_text(relpath: str) -> str:
    return (_root() / safe_name(relpath)).read_text(encoding="utf-8")


def absolute(relpath: str) -> Path:
    return _root() / safe_name(relpath)


def exists(relpath: str) -> bool:
    return absolute(relpath).is_file()


def public_url(relpath: str) -> str:
    """本地开发用 http URL；生产换成私有桶签名 URL，只改这一处。"""
    return f"{get_settings().public_base_url.rstrip('/')}/static/{relpath}"
