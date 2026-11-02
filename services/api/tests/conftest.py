"""测试公共夹具：内存 SQLite + 独立 app 实例。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

os.environ.setdefault("MINGLU_ENV", "test")
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long-for-hs256"
os.environ["SMS_ECHO"] = "1"
os.environ["PAYMENT_MOCK"] = "1"
os.environ["STORAGE_DIR"] = str(SERVICE_ROOT / "var" / "test-storage")
# 测试不许连真实模型：慢、花钱，而且模型输出不稳定（markdown 标记、句式）
# 会让断言 SSE 按句切分的用例随机失败。强制走 LocalProvider。
os.environ["LLM_BASE_URL"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(scope="session")
def client():
    app = create_app(run_seed=False)
    Base.metadata.create_all(get_engine())
    seed(get_sessionmaker()())
    with TestClient(app) as test_client:
        yield test_client


class Actor:
    """一个登录用户，带 token 与常用便捷方法。"""

    def __init__(self, client, phone: str) -> None:
        self.phone = phone
        self.client = client
        self.headers: dict[str, str] = {}
        self._login()

    def _login(self) -> None:
        response = self.client.post("/v1/auth/sms", json={"phone": self.phone})
        assert response.status_code == 200, response.text
        code = _last_code(self.phone)
        response = self.client.post("/v1/auth/login", json={"phone": self.phone, "code": code})
        assert response.status_code == 200, response.text
        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    def post(self, url: str, **kwargs):
        return self.client.post(url, headers=self.headers, **kwargs)

    def get(self, url: str, **kwargs):
        return self.client.get(url, headers=self.headers, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.client.patch(url, headers=self.headers, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.client.delete(url, headers=self.headers, **kwargs)

    def patient(self, name: str = "测试就诊人") -> dict:
        response = self.post(
            "/v1/patients",
            json={"name": name, "relation": "self", "gender": "male"},
        )
        assert response.status_code == 201, response.text
        return response.json()


def _last_code(phone: str) -> str:
    """dev 模式下验证码写进日志；测试直接读 sms_code 表。"""
    from app import models
    from app.database import get_sessionmaker

    session = get_sessionmaker()()
    try:
        record = session.get(models.SmsCode, phone)
        assert record is not None, "验证码未生成"
        return record.code
    finally:
        session.close()


@pytest.fixture
def actor(client) -> Actor:
    counter = next(_phone_counter)
    return Actor(client, f"138{counter:08d}")


_phone_counter = iter(range(10000000, 99999999, 37))


def make_member(client, phone: str, status: str = "active") -> None:
    """直接改 membership 表，模拟已开通 / 已过期 / 被封禁。"""
    from datetime import timedelta

    from app import models
    from app.database import get_sessionmaker, now

    session = get_sessionmaker()()
    try:
        user = session.query(models.User).filter(models.User.phone == phone).one()
        membership = session.query(models.Membership).filter(models.Membership.user_id == user.id).first()
        if membership is None:
            membership = models.Membership(user_id=user.id)
            session.add(membership)
        membership.plan = "monthly"
        membership.status = status
        membership.end_at = now() + timedelta(days=30 if status == "active" else -1)
        session.commit()
    finally:
        session.close()


SEP = chr(10) + chr(10)  # SSE 帧分隔：两个换行


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析成 [(event, data)]。测试断言用解析结果，不做字符串匹配。"""
    frames: list[tuple[str, dict]] = []
    for block in text.strip().split(SEP):
        if not block.strip():
            continue
        event = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if data_lines:
            frames.append((event, json.loads("".join(data_lines))))
    return frames
