"""
明路后端端到端冒烟测试

用途：手动跑一遍主链路，看真实行为（不是单元测试）。
覆盖：发码 → 登录 → 建档 → 流式对话 → 预问诊 → 科室匹配 → 医生列表 → 下单支付

用法：
    # 1. 先启动后端
    cd services/api
    python -m uvicorn app.main:app --port 8011

    # 2. 另开一个终端跑本脚本
    python scripts/smoke_test.py

    # 换端口
    BASE=http://127.0.0.1:8000 python scripts/smoke_test.py
"""

import json
import os
import random
import sqlite3
import sys
from pathlib import Path

import httpx

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SERVICE_ROOT / "minglu.db"
BASE = os.getenv("BASE", "http://127.0.0.1:8011")
PHONE = "138" + "".join(random.choice("0123456789") for _ in range(8))

ok_count = 0
fail_count = 0
problems: list[str] = []


def title(text: str):
    print(f"\n{'=' * 56}\n{text}\n{'=' * 56}")


def check(name: str, cond: bool, detail: str = ""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  [OK]   {name}" + (f"  {detail}" if detail else ""))
    else:
        fail_count += 1
        problems.append(f"{name}  {detail}")
        print(f"  [FAIL] {name}  {detail}")


def read_code_from_db(phone: str) -> str | None:
    """dev 模式下验证码写在 sms_code 表里，直接读库最省事"""
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("SELECT code FROM sms_code WHERE phone = ?", (phone,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def main():
    client = httpx.Client(base_url=BASE, timeout=30.0)

    # ---------- 0. 连通性 ----------
    title("0. 后端连通性")
    try:
        r = client.get("/docs")
        check("GET /docs", r.status_code == 200, f"HTTP {r.status_code}")
    except httpx.ConnectError:
        print(f"\n  连不上 {BASE}")
        print("  先把后端跑起来：python -m uvicorn app.main:app --port 8011")
        sys.exit(1)

    # ---------- 1. 发验证码 ----------
    title("1. 发验证码")
    r = client.post("/v1/auth/sms", json={"phone": PHONE})
    check("POST /v1/auth/sms", r.status_code == 200, f"HTTP {r.status_code} {r.text[:80]}")

    code = read_code_from_db(PHONE)
    check("验证码已落库", bool(code), f"code={code}")

    # 频控：立刻再发一次应被拒
    r2 = client.post("/v1/auth/sms", json={"phone": PHONE})
    check("60 秒内重复发码被拒", r2.status_code == 429, f"HTTP {r2.status_code}")

    if not code:
        print("\n  读不到验证码，后面的步骤无法继续。检查数据库路径：", DB_PATH)
        sys.exit(1)

    # ---------- 2. 登录 ----------
    title("2. 登录")
    r = client.post("/v1/auth/login", json={"phone": PHONE, "code": "000000"})
    check("错误验证码被拒", r.status_code in (400, 401), f"HTTP {r.status_code}")

    r = client.post("/v1/auth/login", json={"phone": PHONE, "code": code})
    check("POST /v1/auth/login", r.status_code == 200, f"HTTP {r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    token = body.get("token")
    check("拿到 token", bool(token))
    # 契约规定：成功响应 request_id 走响应头，错误响应才放进 body
    check("响应带 X-Request-Id 头", bool(r.headers.get("x-request-id")), r.headers.get("x-request-id", "缺失"))

    headers = {"Authorization": f"Bearer {token}"}

    # 无 token 访问受保护接口
    r = client.get("/v1/patients")
    check("无 token 访问被拒", r.status_code in (401, 403), f"HTTP {r.status_code}")

    # ---------- 3. 就诊人 ----------
    title("3. 就诊人档案")
    r = client.get("/v1/patients", headers=headers)
    check("GET /v1/patients", r.status_code == 200, f"HTTP {r.status_code}")
    before = len(r.json().get("items", [])) if r.status_code == 200 else 0

    r = client.post(
        "/v1/patients",
        headers=headers,
        json={"name": "测试阿姨", "relation": "parent", "gender": "female", "birth_date": "1962-03-08"},
    )
    check("POST /v1/patients", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:100]}")
    patient_id = r.json().get("id") if r.status_code in (200, 201) else None
    check("拿到 patient_id", bool(patient_id))

    # ---------- 4. 流式对话 ----------
    title("4. 流式对话（SSE）")
    deltas: list[str] = []
    events: list[str] = []
    try:
        with client.stream(
            "POST",
            "/v1/chat/stream",
            headers=headers,
            json={"patient_id": patient_id, "content": "我最近总是头晕，需要挂什么科？"},
        ) as resp:
            check("POST /v1/chat/stream", resp.status_code == 200, f"HTTP {resp.status_code}")
            check(
                "Content-Type 是 event-stream",
                "text/event-stream" in resp.headers.get("content-type", ""),
                resp.headers.get("content-type", ""),
            )
            cur_event = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    cur_event = line.split(":", 1)[1].strip()
                    events.append(cur_event)
                elif line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    if cur_event == "delta":
                        try:
                            deltas.append(json.loads(raw).get("delta", ""))
                        except json.JSONDecodeError:
                            deltas.append(raw)
    except Exception as e:
        check("SSE 请求", False, repr(e))

    text = "".join(deltas)
    check("收到 delta 事件", len(deltas) > 0, f"{len(deltas)} 个分片")
    check("收到 done 事件", "done" in events, f"事件类型: {sorted(set(events))}")
    check("文本非空", len(text) > 0, f"{len(text)} 字")
    if text:
        print(f"\n  回复前 120 字：{text[:120]}")

    # ---------- 5. 预问诊 ----------
    title("5. 预问诊（最多 6 轮）")
    r = client.post(
        "/v1/preconsult/start",
        headers=headers,
        json={"patient_id": patient_id, "chief_complaint": "头晕乏力五天"},
    )
    check("POST /v1/preconsult/start", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:100]}")
    session = r.json() if r.status_code in (200, 201) else {}
    sid = session.get("id")
    rounds = 0
    while session.get("status") == "asking" and rounds < 8:
        qs = session.get("questions") or []
        if not qs:
            break
        q = qs[0]
        answer = (q.get("options") or ["是"])[0] if q.get("options") else "三天"
        r = client.post(
            f"/v1/preconsult/{sid}/answer",
            headers=headers,
            json={"question_id": q.get("id"), "answer": answer},
        )
        if r.status_code != 200:
            check(f"第 {rounds + 1} 轮回答", False, f"HTTP {r.status_code} {r.text[:100]}")
            break
        session = r.json()
        rounds += 1
    check("预问诊可推进", rounds > 0, f"{rounds} 轮，状态 {session.get('status')}")
    check("状态机没有卡死", session.get("status") in ("asking", "confirming", "done"), str(session.get("status")))

    r = client.post(f"/v1/preconsult/{sid}/finish", headers=headers)
    check("POST /finish 生成病历", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        rec = r.json()
        check("病历有主诉", bool(rec.get("chief_complaint")), str(rec.get("chief_complaint"))[:40])

    # ---------- 6. 科室匹配 ----------
    title("6. 科室匹配")
    r = client.post(
        "/v1/match/departments",
        headers=headers,
        json={"symptoms": ["头晕", "乏力"], "patient_id": patient_id},
    )
    check("POST /v1/match/departments", r.status_code == 200, f"HTTP {r.status_code}")
    matches = r.json().get("items", []) if r.status_code == 200 else []
    check("返回匹配结果", len(matches) > 0, f"{len(matches)} 个")
    check("每条都有可解释理由", all(m.get("reason") for m in matches) if matches else False)
    if matches:
        for m in matches[:3]:
            print(f"    - {m.get('name')}  {m.get('confidence')}  {m.get('reason')}")

    # ---------- 7. 医生列表 ----------
    title("7. 医生列表")
    dept = matches[0]["code"] if matches else None
    r = client.get("/v1/doctors", headers=headers, params={"department_code": dept})
    check("GET /v1/doctors", r.status_code == 200, f"HTTP {r.status_code}")
    doctors = r.json().get("items", []) if r.status_code == 200 else []
    check("该科室有医生", len(doctors) > 0, f"{len(doctors)} 位")
    doctor_id = doctors[0]["id"] if doctors else None

    # ---------- 8. 下单与支付 ----------
    title("8. 下单与支付（mock）")
    if doctor_id:
        r = client.post(
            "/v1/orders",
            headers=headers,
            json={"doctor_id": doctor_id, "patient_id": patient_id},
        )
        check("POST /v1/orders", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:100]}")
        order = r.json() if r.status_code in (200, 201) else {}
        check("金额是整数分", isinstance(order.get("amount_cents"), int), str(order.get("amount_cents")))
        oid = order.get("id")
        if oid:
            r = client.post(f"/v1/orders/{oid}/pay", headers=headers)
            check("POST /pay", r.status_code in (200, 201), f"HTTP {r.status_code}")
            r = client.get(f"/v1/orders/{oid}", headers=headers)
            status = r.json().get("status") if r.status_code == 200 else None
            check("订单已进入已支付", status == "paid", f"status={status}")
    else:
        check("下单", False, "没有可用医生，跳过")

    # ---------- 9. 时间线 ----------
    title("9. 时间线")
    r = client.get("/v1/timeline", headers=headers, params={"patient_id": patient_id})
    check("GET /v1/timeline", r.status_code == 200, f"HTTP {r.status_code}")
    events_list = r.json().get("items", []) if r.status_code == 200 else []
    check("时间线有记录", len(events_list) > 0, f"{len(events_list)} 条")

    # ---------- 10. 会员与白名单 ----------
    title("10. 会员与就医路径白名单")
    r = client.get("/v1/membership", headers=headers)
    check("GET /v1/membership", r.status_code == 200, f"HTTP {r.status_code}")
    check("默认非会员", r.json().get("status") in ("none", "expired") if r.status_code == 200 else False)

    r = client.get("/v1/config/medical-actions", headers=headers)
    check("GET /v1/config/medical-actions", r.status_code == 200, f"HTTP {r.status_code}")
    actions = r.json().get("actions", []) if r.status_code == 200 else []
    check("白名单非空", len(actions) > 0, f"{len(actions)} 个动作")

    # 关键：非会员也能走完就医路径（上面已经验过 preconsult/match/order，这里只做汇总判断）
    check(
        "非会员走通了预问诊/匹配/下单",
        bool(sid) and len(matches) > 0 and bool(doctor_id),
        "付费墙没有误拦就医路径",
    )

    # ---------- 汇总 ----------
    title("结果")
    print(f"  通过 {ok_count} 项，失败 {fail_count} 项")
    if problems:
        print("\n  失败明细：")
        for p in problems:
            print(f"    - {p}")
    print()
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
