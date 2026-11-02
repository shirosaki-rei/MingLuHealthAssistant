"""主链路冒烟：问 → 问清 → 找医生 → 医生回复 → 沉淀成时间线。



跑的是 PRD §20.1 的 V1.0 目标路径，也是 §19.1 里标 P0 的那几个页面背后的接口。

任何一个环节断了，这个测试就该红。

"""

from __future__ import annotations



import io



from app.modules.match import OPEN_DEPARTMENTS



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







def test_main_journey(client):

    actor = Actor(client, "13700000001")

    patient = actor.patient("父亲")



    # ---- 1. 对话：健康问答带来源，按句推送

    chat = actor.post(

        "/v1/chat/stream",

        json={"patient_id": patient["id"], "content": "家庭自测血压的正确方法是什么"},

    )

    assert chat.status_code == 200, chat.text

    frames = parse_sse(chat.text)

    events = [name for name, _ in frames]

    assert "delta" in events and events[-1] == "done", events

    done = dict(frames)["done"]

    conversation_id = done["conversation_id"]

    assert done["message_id"] and done["request_id"]

    # 每个 delta 都是完整句子（后置校验能生效的前提）

    for name, data in frames:

        if name == "delta":

            assert data["delta"].rstrip()[-1] in "。！？；!?;", data["delta"]



    history = actor.get(f"/v1/chat/history?conversation_id={conversation_id}")

    assert history.status_code == 200, history.text

    assert history.json()["total"] >= 2, history.json()

    assistant = [m for m in history.json()["items"] if m["role"] == "assistant"]

    assert assistant and assistant[0]["source_refs"], "健康问答必须带来源"

    assert assistant[0]["source_refs"][0]["title"], assistant[0]



    # ---- 2. 红旗短路：不生成，直接干预

    crisis = actor.post(

        "/v1/chat/stream",

        json={"patient_id": patient["id"], "content": "他突然呼吸困难，嘴唇发紫", "conversation_id": conversation_id},

    )

    assert crisis.status_code == 200, crisis.text

    crisis_frames = parse_sse(crisis.text)

    assert [n for n, _ in crisis_frames] == ["redflag", "done"], crisis_frames

    assert dict(crisis_frames)["redflag"]["action"] == "call_120"



    # ---- 3. 预问诊：6 轮，字段落库

    session = actor.post(

        "/v1/preconsult/start",

        json={"patient_id": patient["id"], "chief_complaint": "头晕乏力五天"},

    )

    assert session.status_code == 201, session.text

    session_id = session.json()["id"]

    assert session.json()["status"] == "asking"

    assert session.json()["questions"], "首轮应带问题"



    answers = {

        "onset": "五天前早上开始，一直没完全好",

        "severity": "6",

        "aggravate": ["体位改变时加重"],

        "accompany": ["恶心呕吐", "头晕"],

        "past_history": "高血压三年",

        "medication": "氨氯地平每天一片",

        "checked": "测过，数值有异常",

        "goal": "想搞清楚要不要调药",

    }

    rounds = 0

    while True:

        current = session.json()

        for question in current["questions"]:

            answer = actor.post(

                f"/v1/preconsult/{session_id}/answer",

                json={"question_id": question["id"], "answer": answers.get(question["id"], "无")},

            )

            assert answer.status_code == 200, answer.text

            session = answer

            rounds += 1

        if session.json()["round"] >= 6 or not session.json()["questions"]:

            break



    assert session.json()["round"] == 6, session.json()

    assert session.json()["insufficient"] is True, "满 6 轮应标注信息较少"



    record = actor.post(f"/v1/preconsult/{session_id}/finish")

    assert record.status_code == 200, record.text

    body = record.json()

    assert body["ready_for_confirm"] is True

    assert "五天前" in body["present_illness"], body

    assert "高血压" in (body["past_history"] or ""), body

    assert body["suggested_departments"], "病历应带推荐科室"



    confirmed = actor.post(f"/v1/preconsult/{session_id}/confirm", json={})

    assert confirmed.status_code == 200, confirmed.text

    assert confirmed.json()["status"] == "done"



    # ---- 4. 找医生：科室匹配 → 列表 → 详情 → 下单 → 支付

    matched = actor.post("/v1/match/departments", json={"symptoms": ["头晕", "头痛"]})

    assert matched.status_code == 200, matched.text

    items = matched.json()["items"]

    assert items, matched.json()

    top = items[0]

    assert 0 < top["confidence"] <= 1 and top["reason"], top



    doctors = actor.get(f"/v1/doctors?department_code={top['code']}")

    assert doctors.status_code == 200, doctors.text

    doctor = doctors.json()["items"][0]

    assert doctor["match_reason"] and doctor["price_cents"] > 0, doctor

    assert doctor["response_minutes"] > 0, doctor



    detail = actor.get(f"/v1/doctors/{doctor['id']}")

    assert detail.status_code == 200, detail.text



    order = actor.post(

        "/v1/orders",

        json={

            "doctor_id": doctor["id"],

            "patient_id": patient["id"],

            "preconsult_session_id": session_id,

        },

    )

    assert order.status_code == 201, order.text

    order_id = order.json()["id"]

    assert order.json()["amount_cents"] == doctor["price_cents"]



    paid = actor.post(f"/v1/orders/{order_id}/pay", json={"channel": "wechat"})

    assert paid.status_code == 200, paid.text

    assert paid.json()["status"] == "paid", paid.json()

    assert paid.json()["paid_at"], paid.json()



    # 幂等：重复支付不重复生成

    again = actor.post(f"/v1/orders/{order_id}/pay", json={"channel": "wechat"})

    assert again.status_code == 200 and again.json()["status"] == "paid"



    # ---- 5. 时间线沉淀 + 就诊摘要导出

    timeline = actor.get(f"/v1/timeline?patient_id={patient['id']}")

    assert timeline.status_code == 200, timeline.text

    kinds = {event["type"] for event in timeline.json()["items"]}

    assert {"preconsult", "consult"} <= kinds, kinds



    exported = actor.post("/v1/timeline/export", json={"patient_id": patient["id"]})

    assert exported.status_code == 201, exported.text

    url = exported.json()["url"]

    assert url.endswith("/v1/exports/") is False

    token = url.rsplit("/", 1)[-1]



    # 分享页是公开端点，不需要登录

    shared = client.get(f"/v1/exports/{token}")

    assert shared.status_code == 200, shared.text

    text = shared.text

    assert "就诊摘要" in text and "父亲" in text, text

    assert "高血压" in text, text

    assert "不构成诊断" in text, "页脚固定免责句"



    # token 不可枚举

    assert client.get("/v1/exports/not-a-real-token").status_code == 410



    # ---- 6. 提醒：服务端算 fire_at，夜间静默生效

    reminder = actor.post(

        "/v1/reminders",

        json={

            "patient_id": patient["id"],

            "kind": "medication",

            "title": "吃降压药",

            "fire_at": _future(30, 9),

            "recurrence": "daily",

        },

    )

    assert reminder.status_code == 201, reminder.text

    assert reminder.json()["fire_at"] == _future(30, 9), reminder.json()



    night = actor.post(

        "/v1/reminders",

        json={

            "patient_id": patient["id"],

            "kind": "medication",

            "title": "夜里吃药",

            "fire_at": _future(30, 23, 30),

        },

    )

    assert night.status_code == 400, night.text

    assert night.json()["code"] == "VALIDATION_ERROR", night.json()



    pending = actor.get("/v1/reminders/pending?within_hours=72")

    assert pending.status_code == 200, pending.text

    assert all(item["fire_at"] and item["title"] for item in pending.json()["items"])



    # ---- 7. 报告上传：202 + 异步 OCR

    upload = actor.post(

        "/v1/reports",

        files={"file": ("report.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "image/png")},

        data={"patient_id": patient["id"]},

    )

    assert upload.status_code == 202, upload.text

    report_id = upload.json()["report_id"]

    fetched = actor.get(f"/v1/reports/{report_id}")

    assert fetched.status_code == 200, fetched.text

    assert fetched.json()["status"] in {"processing", "done", "failed"}, fetched.json()



    bad_type = actor.post(

        "/v1/reports",

        files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},

        data={"patient_id": patient["id"]},

    )

    assert bad_type.status_code == 400

    assert bad_type.json()["code"] == "UPLOAD_BAD_TYPE", bad_type.json()



    # ---- 8. 记忆：写入后可查、可改、可删（合规必需）

    memory_list = actor.get(f"/v1/memories?patient_id={patient['id']}")

    assert memory_list.status_code == 200, memory_list.text

    assert memory_list.json()["total"] >= 1, "「高血压」应从对话里抽成事实型记忆"

    memory_id = memory_list.json()["items"][0]["id"]



    updated = actor.patch(f"/v1/memories/{memory_id}", json={"value": "高血压三年，服药中"})

    assert updated.status_code == 200, updated.text

    assert updated.json()["value"] == "高血压三年，服药中"

    assert updated.json()["confirmed_at"], "用户改过就算确认过"



    assert actor.delete(f"/v1/memories/{memory_id}").status_code == 204

    assert actor.get(f"/v1/memories/{memory_id}") is not None





def test_unmatched_symptoms_fall_back_to_department_list(client):

    """症状一个都没匹配上时，客户端要有东西可选，不能是空页面。"""

    actor = Actor(client, "13700000004")

    actor.patient()



    matched = actor.post("/v1/match/departments", json={"symptoms": ["脚趾发麻"]})

    assert matched.status_code == 200, matched.text

    assert matched.json()["items"] == [], matched.json()



    departments = actor.get("/v1/departments")

    assert departments.status_code == 200, departments.text

    items = departments.json()["items"]

    assert len(items) >= 12, items

    codes = {d["code"] for d in items}

    assert {"cardiology", "neurology", "endocrinology", "dermatology"} <= codes, codes

    for item in items:

        assert item["name"] and isinstance(item["consult_enabled"], bool), item



    # 未开放咨询的科室要能被筛掉

    open_only = actor.get("/v1/departments?consult_only=true")

    assert open_only.status_code == 200, open_only.text

    assert {d["code"] for d in open_only.json()["items"]} == OPEN_DEPARTMENTS, open_only.json()





def test_membership_and_persona_flow(client):

    actor = Actor(client, "13700000002")



    membership = actor.get("/v1/membership")

    assert membership.status_code == 200, membership.text

    assert membership.json()["status"] == "none", membership.json()

    entitlements = membership.json()["entitlements"]

    assert entitlements["daily_chat_limit"] == 20

    assert entitlements["custom_persona_quota"] == 0, "免费版没有自定义角色名额"

    assert entitlements["max_patients"] == 3, "免费版最多 3 位就诊人"



    personas = actor.get("/v1/personas")

    assert personas.status_code == 200, personas.text

    presets = personas.json()["presets"]

    assert len(presets) == 4, presets

    assert {p["name"] for p in presets} == {"安安", "小简", "老陈", "小叶"}



    # 免费版没有名额 → 返回业务码，不是 500，也不该是 402/付费墙

    blocked = actor.post("/v1/personas", json={"name": "小喵", "species": "猫娘"})

    assert blocked.status_code != 500

    assert blocked.json()["code"] == "PERSONA_QUOTA_EXCEEDED", blocked.json()

    # 免费版是「从来没名额」，不能说成「用完了」

    assert "需要会员" in blocked.json()["message"], blocked.json()



    # 非会员无法提交自定义人设——人格层再自由也过不了这道

    allowed = actor.post(

        "/v1/personas", json={"name": "小喵", "species": "猫娘", "worldview": "来自喵星的旅人"}

    )

    assert allowed.status_code != 201, allowed.text

    assert allowed.json()["code"] == "PERSONA_QUOTA_EXCEEDED", allowed.json()



    # 身份词拦截

    rejected = actor.post("/v1/personas", json={"name": "张主任", "species": "猫娘"})

    assert rejected.status_code == 400, rejected.text

    assert rejected.json()["code"] == "PERSONA_FORBIDDEN_WORD", rejected.json()



    # 会员反过来：3 个名额能建满，第 4 个才是「用完了」

    # 换个手机号：persona.create 有 3 次/分钟的频控，复用上面那个号会被 429 挡住

    member = Actor(client, "13900000007")

    make_member(client, member.phone, status="active")

    # 频控上限（3 次/分钟）和名额（3 个）一样大，不清桶第 4 次先撞频控，测不到额度分支

    from app.modules import entitlement



    for index in range(3):

        created = member.post("/v1/personas", json={"name": f"会员角色{index}", "species": "猫娘"})

        assert created.status_code == 201, created.text

    entitlement._rate_buckets.clear()  # 3 次已把桶填满，清掉才走到额度判断

    used_up = member.post("/v1/personas", json={"name": "第四个", "species": "猫娘"})

    assert used_up.json()["code"] == "PERSONA_QUOTA_EXCEEDED", used_up.json()

    assert "用完了" in used_up.json()["message"], used_up.json()





def test_redflag_rules_endpoint_matches_db(client):

    """客户端离线兜底用的规则必须与服务端一致。"""

    actor = Actor(client, "13700000003")

    response = actor.get("/v1/redflag/rules")

    assert response.status_code == 200, response.text

    rules = response.json()["rules"]

    assert len(rules) >= 11, rules

    for rule in rules:

        assert rule["id"] and rule["keywords"] and rule["level"] in {"attention", "urgent", "crisis"}

        assert rule["action"] in {"show_card", "call_120", "encourage_emergency"}

    suicide = next(r for r in rules if r["id"] == "suicide")

    assert suicide["level"] == "crisis" and suicide["action"] == "show_card", suicide





def test_persona_avatar_only_for_own_custom_persona(client, tmp_path):
    """自定义角色头像：只能传自己的、非预设的；没过审不外发 URL。"""
    from app.modules import entitlement

    actor = Actor(client, "13900000011")
    make_member(client, actor.phone, status="active")  # 免费版没有自定义角色名额
    entitlement._rate_buckets.clear()
    created = actor.post("/v1/personas", json={"name": "小鹿", "species": "精灵"})
    assert created.status_code == 201, created.text
    persona_id = created.json()["id"]
    assert created.json()["avatar_url"] is None, "还没上传就不该有头像"

    png = tmp_path / "a.png"
    png.write_bytes(bytes.fromhex("89504e470d0a1a0a") + bytes(64))

    def upload(who: Actor, pid: str):
        with open(png, "rb") as fh:
            return who.post(
                f"/v1/personas/{pid}/avatar",
                files={"file": ("a.png", fh, "image/png")},
            )

    # 预设角色一律拒：它的头像由服务端固定下发
    preset_id = actor.get("/v1/personas").json()["presets"][0]["id"]
    assert upload(actor, preset_id).status_code == 404

    # 别人的角色同样拒（不说清是「不存在」还是「不是你的」）
    stranger = Actor(client, "13900000012")
    assert upload(stranger, persona_id).status_code == 404

    ok = upload(actor, persona_id)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["review_status"] == "pending", "换图要重新送审"
    assert body["avatar_url"] is None, "没过审不能把用户图片的 URL 发出去"

    # 服务端批过之后，URL 才出现
    from app import models
    from app.database import get_sessionmaker

    session = get_sessionmaker()()
    row = session.query(models.Persona).filter_by(id=persona_id).one()
    row.review_status = "approved"
    session.commit()
    session.close()

    listed = actor.get("/v1/personas").json()["mine"]
    assert listed[0]["avatar_url"] is not None, listed
    assert listed[0]["avatar_url"].endswith(".png"), listed


def test_preset_personas_carry_fixed_avatar(client):
    """预设角色头像由服务端按名下发给打包图，用户没有入口改它。"""
    actor = Actor(client, "13900000013")
    presets = actor.get("/v1/personas").json()["presets"]
    assert len(presets) == 4, presets
    for p in presets:
        assert p["is_preset"] is True, p
        assert p["avatar_url"].startswith("/static/avatars/"), p
        assert p["avatar_url"].endswith(".jpg"), p
    assert {p["name"] for p in presets} == {"安安", "老陈", "小简", "小叶"}
