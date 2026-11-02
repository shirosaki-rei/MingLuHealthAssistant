"""提醒必须按就诊人过滤。

曾经 `GET /v1/reminders/pending` 只按 user_id 过滤，而 Reminder 一直有
patient_id——账号下三个就诊人的提醒全混在一起，首页"今天的安排"切人完全不变化。
这条用例守两件事：别人的提醒不出现、别人的 patient_id 读不到（404 而不是空列表）。
"""
from __future__ import annotations

from .conftest import Actor
from .test_journey import _future


def _create(actor: Actor, patient_id: str, title: str) -> dict:
    resp = actor.post(
        "/v1/reminders",
        json={
            "patient_id": patient_id,
            "kind": "medication",
            "title": title,
            "content": "饭后",
            # 必须在 pending 的 within_hours 窗口内，否则查不到（第一次写成 2030 年，
            # 创建成功了但 pending 返回空，白排查一轮）。日期也不能写死，会随时间过期。
            "fire_at": _future(1, 9),
            "recurrence": "once",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_pending_is_scoped_to_patient(client):
    actor = Actor(client, "13800006001")
    mine = actor.patient("陈志远")
    other = actor.patient("陈志远母亲")

    _create(actor, mine["id"], "我的降压药")
    _create(actor, other["id"], "母亲的钙片")

    rows = actor.get(f"/v1/reminders/pending?patient_id={mine['id']}").json()["items"]
    assert [r["title"] for r in rows] == ["我的降压药"], rows

    # 换就诊人必须换内容，这是首页"今天的安排"能用的前提
    rows2 = actor.get(f"/v1/reminders/pending?patient_id={other['id']}").json()["items"]
    assert [r["title"] for r in rows2] == ["母亲的钙片"], rows2


def test_cannot_read_other_accounts_patient(client):
    owner = Actor(client, "13800006002")
    stranger = Actor(client, "13800006003")
    patient = owner.patient("陈志远")
    _create(owner, patient["id"], "我的降压药")

    resp = stranger.get(f"/v1/reminders/pending?patient_id={patient['id']}")
    # 不说清是"不存在"还是"不是你的"，别给枚举他人资产的口子
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "NOT_FOUND", resp.text

    # 漏传 patient_id 要明确报错，而不是悄悄退回"返回全部"
    missing = stranger.get("/v1/reminders/pending")
    assert missing.status_code == 422, missing.text
