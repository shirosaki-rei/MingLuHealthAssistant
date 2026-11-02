"""契约测试：付费墙是产品红线，光靠 code review 守不住（契约 README 的 12 条）。

核心断言：就医路径在任何会员状态下都必须返回成功；唯一该拒绝的是未登录。
"""
from __future__ import annotations

import pytest

from .conftest import Actor, make_member, parse_sse

def _future(days: int, hour: int = 9, minute: int = 0) -> str:
    """days 天后的时刻，ISO 8601 带 +08:00。

    提醒的 fire_at 必须晚于当前时间，写死日期会随现实时间过期——
    2026-11-01 就刚让一条用例红得很莫名。动态算才不会再犯。
    """
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=8))
    return (datetime.now(tz) + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).isoformat()



# 就医路径全链路：(方法, 路径, 请求体)。patient_id 由 fixture 注入。
MEDICAL_PATH_CASES = [
    ("preconsult.start", "POST", "/v1/preconsult/start",
     {"patient_id": "{pid}", "chief_complaint": "头晕乏力五天"}),
    ("patient.list", "GET", "/v1/patients", None),
    ("match.departments", "POST", "/v1/match/departments", {"symptoms": ["头晕"]}),
    ("doctor.list", "GET", "/v1/doctors?department_code=neurology", None),
    ("timeline.read", "GET", "/v1/timeline?patient_id={pid}", None),
    ("memory.read", "GET", "/v1/memories?patient_id={pid}", None),
    ("me.update", "PATCH", "/v1/me", {"nickname": "就医路径测试昵称"}),
    # timeline.export 不在这张表里：区间内没内容时它按契约返回 400，
    # 而这张表断言的是「不被会员状态拦」。见循环后面的导出断言。
]


@pytest.mark.parametrize("membership_status", ["none", "expired", "banned"])
def test_medical_path_never_blocked(client, membership_status):
    """#1~#6 未开通 / 已过期 / 被封禁：就医路径全部可用。"""
    actor = Actor(client, f"139{abs(hash(membership_status)) % 10**8:08d}"[:11])
    make_member(client, actor.phone, status=membership_status)
    patient = actor.patient()

    for action, method, path, body in MEDICAL_PATH_CASES:
        url = path.format(pid=patient["id"])
        if body:
            body = {k: (patient["id"] if v == "{pid}" else v) for k, v in body.items()}
        response = actor.post(url, json=body) if method == "POST" else actor.get(url)
        if method == "PATCH":
            response = actor.patch(url, json=body)
        assert response.status_code in (200, 201), f"{action} 被拦了：{response.status_code} {response.text}"
        payload = response.json()
        assert payload.get("code") is None, f"{action} 返回了业务错误码 {payload.get('code')}"

    # 导出同属就医路径，但得先有内容：区间内空空的，服务端按契约 1.3.1 返回 400
    started = actor.post(
        "/v1/preconsult/start", json={"patient_id": patient["id"], "chief_complaint": "头晕乏力五天"}
    )
    session_id = started.json()["id"]
    actor.post(f"/v1/preconsult/{session_id}/finish")
    actor.post(f"/v1/preconsult/{session_id}/confirm", json={})
    exported = actor.post("/v1/timeline/export", json={"patient_id": patient["id"]})
    assert exported.status_code == 201, exported.text

    # 医生详情与下单同属就医路径
    doctors = actor.get("/v1/doctors?department_code=neurology").json()["items"]
    assert doctors, "种子数据里应该有神经内科医生"
    detail = actor.get(f"/v1/doctors/{doctors[0]['id']}")
    assert detail.status_code == 200, detail.text

    order = actor.post(
        "/v1/orders", json={"doctor_id": doctors[0]["id"], "patient_id": patient["id"]}
    )
    assert order.status_code == 201, order.text
    paid = actor.post(f"/v1/orders/{order.json()['id']}/pay", json={})
    assert paid.status_code == 200, paid.text


def test_unauthenticated_is_the_only_rejection(client):
    """#7 未登录：必须 401，这是唯一该拒绝的。"""
    for method, path in (
        ("get", "/v1/patients"),
        ("post", "/v1/preconsult/start"),
        ("get", "/v1/membership"),
        ("get", "/v1/chat/history"),
        ("get", "/v1/config/medical-actions"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, f"{method} {path} 未登录应 401，实际 {response.status_code}"
        body = response.json()
        assert body["code"] == "UNAUTHORIZED", body
        assert body["message"] and body["request_id"], body


def test_paywall_misblock_rate_is_zero(client):
    """红线指标：付费墙误拦率 0。遍历白名单里每个 action 对应的端点。"""
    from app.modules.entitlement import MEDICAL_ACTIONS

    actor = Actor(client, "13900000001")
    patient = actor.patient()
    actions_response = actor.get("/v1/config/medical-actions")
    assert actions_response.status_code == 200
    declared = set(actions_response.json()["actions"])

    # 白名单里的每个 action 都要能在未开通会员下跑通
    assert MEDICAL_ACTIONS == declared, "代码白名单与契约返回不一致"
    assert "patient.create" in declared and "order.pay" in declared and "memory.delete" in declared


def test_chat_limit_does_not_block_features(client, monkeypatch):
    """#8 限额用尽：对话仍可进行，限额只限轮数不限功能。"""
    actor = Actor(client, "13900000002")
    patient = actor.patient()

    # 把免费额度压到 1，先烧掉一轮
    from app.modules import entitlement

    monkeypatch.setattr(entitlement, "FREE_CHAT_DAILY", 1)
    response = actor.post(
        "/v1/chat/stream",
        json={"patient_id": patient["id"], "content": "量血压的正确方法是什么"},
    )
    assert response.status_code == 200, response.text
    assert "event: delta" in response.text, response.text

    # 就医路径依旧畅通
    assert actor.get("/v1/preconsult/start") is not None
    assert actor.get("/v1/doctors?department_code=neurology").status_code == 200
    assert actor.get(f"/v1/timeline?patient_id={patient['id']}").status_code == 200


def test_patient_limit_returns_business_code_not_500(client):
    """#9 就诊人满额：明确业务码，不是 500。免费版 3 位、会员 6 位。"""
    from app.modules.entitlement import MAX_PATIENTS, MAX_PATIENTS_FREE

    actor = Actor(client, "13900000003")
    for index in range(MAX_PATIENTS_FREE):
        response = actor.post(
            "/v1/patients",
            json={"name": f"家人{index}", "relation": "parent", "gender": "female"},
        )
        assert response.status_code == 201, response.text

    overflow = actor.post(
        "/v1/patients", json={"name": "第四个", "relation": "other", "gender": "unknown"}
    )
    assert overflow.status_code != 500
    body = overflow.json()
    assert body["code"] == "PATIENT_LIMIT_EXCEEDED", body
    assert body["details"]["limit"] == MAX_PATIENTS_FREE, body

    # 会员多 3 个名额，满额值跟着变
    make_member(client, "13900000003", status="active")
    for index in range(MAX_PATIENTS - MAX_PATIENTS_FREE):
        response = actor.post(
            "/v1/patients",
            json={"name": f"会员家人{index}", "relation": "child", "gender": "male"},
        )
        assert response.status_code == 201, response.text
    again = actor.post(
        "/v1/patients", json={"name": "第七个", "relation": "other", "gender": "unknown"}
    )
    assert again.json()["details"]["limit"] == MAX_PATIENTS, again.json()


def test_redflag_intervenes_regardless_of_membership(client):
    """#10 红旗命中：无论会员状态都弹干预卡。"""
    for phone, status in (("13900000004", "none"), ("13900000005", "active")):
        actor = Actor(client, phone)
        make_member(client, phone, status=status)
        patient = actor.patient()
        response = actor.post(
            "/v1/chat/stream",
            json={"patient_id": patient["id"], "content": "我突然胸痛，还出汗放射到左臂"},
        )
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        events = [name for name, _ in frames]
        assert events == ["redflag", "done"], events
        redflag = dict(frames)["redflag"]
        assert redflag["level"] == "urgent", redflag
        assert redflag["action"] == "call_120", redflag


def test_export_refuses_when_timeline_is_empty(actor):
    """契约 1.3.1：区间内一条记录都没有就不给链接，别生成空摘要。"""
    patient = actor.patient()

    empty = actor.post("/v1/timeline/export", json={"patient_id": patient["id"]})
    assert empty.status_code == 400, empty.text
    assert empty.json()["code"] == "VALIDATION_ERROR", empty.json()

    # 有内容了才放行
    started = actor.post(
        "/v1/preconsult/start", json={"patient_id": patient["id"], "chief_complaint": "头晕乏力五天"}
    )
    session_id = started.json()["id"]
    actor.post(f"/v1/preconsult/{session_id}/finish")
    actor.post(f"/v1/preconsult/{session_id}/confirm", json={})
    filled = actor.post("/v1/timeline/export", json={"patient_id": patient["id"]})
    assert filled.status_code == 201, filled.text
    assert filled.json()["url"], filled.json()


def test_nickname_update_persists_and_phone_is_untouchable(actor):
    """改昵称要真落库；手机号是登录凭据，PATCH /v1/me 不能碰。"""
    from app import models
    from app.database import get_sessionmaker

    session = get_sessionmaker()()
    try:
        user = session.query(models.User).filter(models.User.phone == actor.phone).one()
        phone_before = user.phone
    finally:
        session.close()

    saved = actor.patch("/v1/me", json={"nickname": " 小明 "})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["nickname"] == "小明", "昵称要 strip，不能把空格存进去"
    assert body["phone_masked"] == f"{phone_before[:3]}****{phone_before[-4:]}"

    # 再读一次库确认落库（不是只在响应里改了）
    session = get_sessionmaker()()
    try:
        user = session.query(models.User).filter(models.User.phone == actor.phone).one()
        assert user.nickname == "小明"
        assert user.phone == phone_before, "手机号不能被这个接口改掉"
    finally:
        session.close()

    # 空昵称 / 超长都要被挡在 422，不能落库
    assert actor.patch("/v1/me", json={"nickname": ""}).status_code == 422
    assert actor.patch("/v1/me", json={"nickname": "x" * 41}).status_code == 422


def test_avatar_upload_persists_and_is_served(actor):
    """头像：上传后落库 + 返回的 URL 真能取到字节（/static 挂载别丢）。"""
    from app import models
    from app.database import get_sessionmaker

    # 一张 1x1 的 png
    png = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                        "01f15c4890000000a49444154789c6300010000050001"
                        "0d0a2db40000000049454e44ae426082")

    uploaded = actor.post(
        "/v1/me/avatar", files={"file": ("a.png", png, "image/png")}
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["avatar_url"], body
    assert body["avatar_url"].endswith(".png"), body

    session = get_sessionmaker()()
    try:
        user = session.query(models.User).filter(models.User.phone == actor.phone).one()
        assert user.avatar_path, "头像路径要落库"
    finally:
        session.close()

    # URL 是给客户端 <image> 用的，取不到字节就等于没传
    fetched = actor.client.get(body["avatar_url"])
    assert fetched.status_code == 200, fetched.status_code
    assert fetched.content == png

    # 类型与大小沿用报告上传的两条校验
    assert actor.post(
        "/v1/me/avatar", files={"file": ("a.txt", b"hello", "text/plain")}
    ).json()["code"] == "UPLOAD_BAD_TYPE"
    assert actor.post(
        "/v1/me/avatar", files={"file": ("a.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")}
    ).json()["code"] == "UPLOAD_TOO_LARGE"


def test_timestamps_are_iso8601_with_timezone(client):
    """#11 时间字段全部 ISO 8601 带时区，无毫秒时间戳。"""
    actor = Actor(client, "13900000006")
    patient = actor.patient()
    created_at = patient["created_at"]
    assert created_at.endswith("+08:00"), created_at
    assert "T" in created_at and "." not in created_at, created_at
    assert not created_at.isdigit(), "不能是毫秒时间戳"

    reminder = actor.post(
        "/v1/reminders",
        json={
            "patient_id": patient["id"],
            "kind": "medication",
            "title": "吃降压药",
            # 动态算：写死日期会随现实时间过期（2026-11-01 就刚过期过一次，
            # 被"触发时间需晚于当前时间"挡下，测试红得很莫名）
            "fire_at": _future(1, 9),
        },
    )
    assert reminder.status_code == 201, reminder.text
    assert reminder.json()["fire_at"].endswith("+08:00"), reminder.json()


def test_error_body_shape(client):
    """#12 错误响应必含 code / message / request_id。"""
    response = client.get("/v1/patients")
    assert response.status_code == 401
    body = response.json()
    assert set(body) >= {"code", "message", "request_id"}, body
    assert response.headers["X-Request-Id"] == body["request_id"], "响应头与错误体要一致"

    missing = client.post("/v1/auth/login", json={"phone": "13800138000", "code": "000000"})
    assert missing.status_code == 400
    assert missing.json()["code"] in {"SMS_CODE_INVALID", "SMS_CODE_EXPIRED"}, missing.json()
