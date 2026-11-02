"""会话列表与归档回归（第三期）。

守三件事：列表带得上摘要和条数、归档后从主列表消失且消息不删、
不能动别人的会话（越权改 hidden 必须 404）。
"""
from __future__ import annotations

import json

from .conftest import Actor


def _chat(actor: Actor, patient_id: str, text: str) -> str:
    """发一句话，拿回 conversation_id。"""
    conversation_id = ""
    with actor.client.stream(
        "POST", "/v1/chat/stream", headers=actor.headers, json={"patient_id": patient_id, "content": text}
    ) as resp:
        assert resp.status_code == 200, resp.text
        for line in resp.iter_lines():
            if line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
                if payload.get("conversation_id"):
                    conversation_id = payload["conversation_id"]
    assert conversation_id, "done 帧里必须有 conversation_id"
    return conversation_id


def test_list_and_archive_roundtrip(client):
    actor = Actor(client, "13800007001")
    patient = actor.patient("陈志远")
    conversation_id = _chat(actor, patient["id"], "我最近睡不好")

    listed = actor.get("/v1/conversations", params={"patient_id": patient["id"]})
    assert listed.status_code == 200, listed.text
    rows = listed.json()["items"]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["id"] == conversation_id
    assert row["patient_name"] == patient["name"]
    assert row["message_count"] >= 2, "一问一答至少两条"
    assert row["last_message"] != "", "列表要有末条消息摘要"
    assert row["hidden"] is False
    # 没有传助手的会话，助手名退回默认值而不是空
    assert row["persona_name"] == "明路助手", row

    # 归档：从主列表消失
    archived = actor.patch(f"/v1/conversations/{conversation_id}", json={"hidden": True})
    assert archived.status_code == 200, archived.text
    assert archived.json()["hidden"] is True

    after = actor.get("/v1/conversations", params={"patient_id": patient["id"]})
    assert after.json()["items"] == [], "归档后不该出现在主列表"

    # include_hidden 能查回来，且消息一条没删
    hidden = actor.get(
        "/v1/conversations", params={"patient_id": patient["id"], "include_hidden": True}
    )
    assert len(hidden.json()["items"]) == 1
    history = actor.get(f"/v1/chat/history?conversation_id={conversation_id}")
    assert history.json()["total"] >= 2, "归档不是删除，消息必须还在"

    # 恢复
    restored = actor.patch(f"/v1/conversations/{conversation_id}", json={"hidden": False})
    assert restored.json()["hidden"] is False
    assert len(actor.get("/v1/conversations", params={"patient_id": patient["id"]}).json()["items"]) == 1


def test_cannot_archive_someone_elses_conversation(client):
    owner = Actor(client, "13800007002")
    stranger = Actor(client, "13800007003")
    patient = owner.patient("陈志远")
    conversation_id = _chat(owner, patient["id"], "随便聊聊")

    resp = stranger.patch(f"/v1/conversations/{conversation_id}", json={"hidden": True})
    # 不说清是"不存在"还是"不是你的"：别给枚举他人资产的口子
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "NOT_FOUND", resp.text

    # 别人的会话也不该出现在我的列表里
    mine = stranger.get("/v1/conversations")
    assert all(item["id"] != conversation_id for item in mine.json()["items"])
