"""
演示数据预热

作用：造一个「有历史」的账号，演示打开就有内容，不用现场攒数据。
会在目标库里创建：3 位就诊人、2 段对话、1 份预问诊病历、1 笔已支付订单、3 条提醒（一人一条）。

用法：
    # 1. 后端先跑起来（本地库）
    cd services/api
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

    # 2. 另开终端
    .venv\\Scripts\\python.exe scripts\\demo_seed.py

    # 指定后端地址
    set BASE=http://127.0.0.1:8000 && .venv\\Scripts\\python.exe scripts\\demo_seed.py

跑完会打印演示用的手机号和当前验证码，登录时直接填。
"""

import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

SERVICE_ROOT = Path(__file__).resolve().parent.parent
# 默认与后端同库；用临时库验证时可覆盖
DB_PATH = Path(os.getenv("DEMO_DB", str(SERVICE_ROOT / "minglu.db")))
BASE = os.getenv("BASE", "http://127.0.0.1:8000")
PHONE = os.getenv("DEMO_PHONE", "13800000001")


def read_code(phone: str) -> str | None:
    """本地开发读 SQLite；容器里读 DATABASE_URL 指向的 PostgreSQL。

    容器里没有 minglu.db，直连 sqlite 会在新建的空库上报 no such table，
    表现为「读不到验证码」——虽然码其实已经在 PG 里了。
    """
    dsn = os.getenv("DATABASE_URL", "")
    if dsn.startswith(("postgresql", "postgres")):
        import psycopg

        with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://")) as con:
            row = con.execute("SELECT code FROM sms_code WHERE phone = %s", (phone,)).fetchone()
            return row[0] if row else None
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("SELECT code FROM sms_code WHERE phone = ?", (phone,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def main():
    # 流式对话是逐 token 产出的，一整段回复可能超过 40s（首次调用还要算上
    # provider 冷启动）。read 超时放宽到 120s，connect 仍收紧到 10s。
    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(120.0, connect=10.0))
    print(f"目标后端：{BASE}")
    print(f"演示手机号：{PHONE}\n")

    # ---------- 登录 ----------
    r = client.post("/v1/auth/sms", json={"phone": PHONE})
    if r.status_code == 429:
        print("  该号码 60 秒内已发过码，直接复用上一条")
    elif r.status_code != 200:
        print(f"  发码失败 HTTP {r.status_code}: {r.text[:100]}")
        sys.exit(1)

    code = read_code(PHONE)
    if not code:
        print("  读不到验证码，检查数据库路径：", DB_PATH)
        sys.exit(1)

    r = client.post("/v1/auth/login", json={"phone": PHONE, "code": code})
    if r.status_code != 200:
        print(f"  登录失败 HTTP {r.status_code}: {r.text[:100]}")
        sys.exit(1)
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    print(f"  登录成功，验证码 {code}\n")

    # ---------- 就诊人 ----------
    existing = client.get("/v1/patients", headers=h).json().get("items", [])
    by_relation = {p.get("relation"): p for p in existing}
    seeds = [
        {"name": "陈志远", "relation": "self", "gender": "male", "birth_date": "1988-06-12",
         "height_cm": 176, "weight_kg": 78, "chronic_conditions": ["高血压"]},
        {"name": "陈母", "relation": "parent", "gender": "female", "birth_date": "1961-02-20",
         "height_cm": 158, "weight_kg": 61, "chronic_conditions": ["2 型糖尿病", "高血压"],
         "allergy_notes": "青霉素过敏"},
        {"name": "陈小雨", "relation": "child", "gender": "female", "birth_date": "2017-09-03",
         "height_cm": 122, "weight_kg": 23, "guardian_consent": True},
    ]
    patients = list(existing)
    for s in seeds:
        if s["relation"] in by_relation:
            continue
        r = client.post("/v1/patients", headers=h, json=s)
        if r.status_code in (200, 201):
            created = r.json()
            patients.append(created)
            by_relation[s["relation"]] = created
            print(f"  就诊人 +{s['name']}")
        else:
            print(f"  就诊人 {s['name']} 创建失败：{r.text[:80]}")
    if not patients:
        print("  没有就诊人，无法继续")
        sys.exit(1)
    me = by_relation.get("self") or patients[0]
    parent = by_relation.get("parent") or patients[0]
    child = by_relation.get("child") or patients[0]
    pid, parent_id, child_id = me["id"], parent["id"], child["id"]

    # ---------- 对话 ----------
    convs = [
        (parent_id, "我妈最近总说站起来头晕，量了血压也偏低，需要去医院吗？"),
        (pid, "体检报告上写甲状腺结节 TI-RADS 3 类，要紧吗？"),
    ]
    for idx, (who, text) in enumerate(convs, 1):
        chars = 0
        with client.stream("POST", "/v1/chat/stream", headers=h,
                           json={"patient_id": who, "content": text}) as resp:
            if resp.status_code != 200:
                print(f"  对话 {idx} 失败 HTTP {resp.status_code}")
                continue
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    try:
                        chars += len(json.loads(raw).get("delta", ""))
                    except json.JSONDecodeError:
                        pass
        print(f"  对话 {idx} +1（回复 {chars} 字）")

    # ---------- 预问诊 ----------
    r = client.post("/v1/preconsult/start", headers=h,
                    json={"patient_id": parent_id, "chief_complaint": "反复头晕两周，起身时明显"})
    if r.status_code in (200, 201):
        session = r.json()
        sid = session["id"]
        rounds = 0
        while session.get("status") == "asking" and rounds < 8:
            qs = session.get("questions") or []
            if not qs:
                break
            q = qs[0]
            opts = q.get("options") or []
            answer = opts[0] if opts else "两周左右，起身时明显"
            r2 = client.post(f"/v1/preconsult/{sid}/answer", headers=h,
                             json={"question_id": q.get("id"), "answer": answer})
            if r2.status_code != 200:
                break
            session = r2.json()
            rounds += 1
        r3 = client.post(f"/v1/preconsult/{sid}/finish", headers=h)
        print(f"  预问诊完成（{rounds} 轮），病历生成 {'成功' if r3.status_code == 200 else '失败'}")
    else:
        print(f"  预问诊启动失败：{r.text[:80]}")

    # ---------- 下单 ----------
    r = client.post("/v1/match/departments", headers=h,
                    json={"symptoms": ["头晕", "起身时明显"], "patient_id": parent_id})
    dept = None
    if r.status_code == 200 and r.json().get("items"):
        dept = r.json()["items"][0]["code"]
        print(f"  匹配科室：{r.json()['items'][0]['name']}（置信度 {r.json()['items'][0]['confidence']}）")

    doctor_id = None
    if dept:
        r = client.get("/v1/doctors", headers=h, params={"department_code": dept})
        docs = r.json().get("items", []) if r.status_code == 200 else []
        online = [d for d in docs if d.get("online_status") == "online"]
        if online:
            doctor_id = online[0]["id"]

    if doctor_id:
        r = client.post("/v1/orders", headers=h,
                        json={"doctor_id": doctor_id, "patient_id": parent_id})
        if r.status_code in (200, 201):
            oid = r.json()["id"]
            client.post(f"/v1/orders/{oid}/pay", headers=h)
            print(f"  订单 +1 并已支付（{r.json().get('amount_cents', 0) / 100:.0f} 元）")
    else:
        print("  没有在线医生，跳过下单")

    # ---------- 提醒 ----------
    # 必须显式带时区：容器里 datetime.now() 拿到的是 UTC，而后端把不带时区的时间
    # 一律按 Asia/Shanghai 解释，于是「1 小时后」会被判成「8 小时前」直接 400。
    now = datetime.now(timezone(timedelta(hours=8)))

    def safe_time(hours: float) -> datetime:
        """避开 23:00-7:00 静默时段，撞上了就顺延到次日 8 点"""
        t = now + timedelta(hours=hours)
        if t.hour >= 23 or t.hour < 7:
            t = (t + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        return t

    plans = [
        (pid, "吃降压药", "氨氯地平 5mg，饭后", safe_time(1)),
        (parent_id, "测空腹血糖", "起床后、早餐前", safe_time(2)),
        (child_id, "吃维生素 D", "每天一粒，随餐", safe_time(3)),
    ]
    # 三个人各建各的：提醒从创建起就按 patient_id 隔离（服务端 /v1/reminders/pending
    # 按人过滤），早先三条全挂在 parent_id 上，隔离修好后另外两人首页就是空的，
    # 反倒演示不出「数据按人分开」。
    for patient_id, title, content, at in plans:
        r = client.post("/v1/reminders", headers=h, json={
            "patient_id": patient_id, "kind": "medication", "title": title,
            "content": content, "fire_at": at.isoformat(), "recurrence": "daily",
        })
        if r.status_code in (200, 201):
            print(f"  提醒 +1：{title}")
        else:
            print(f"  提醒「{title}」失败 HTTP {r.status_code}：{r.text[:100]}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 48)
    print("演示账号已就绪")
    print("=" * 48)
    print(f"  手机号：{PHONE}")
    print(f"  验证码：{code}（5 分钟内有效）")
    print(f"  就诊人：{len(patients)} 位（本人 / 母亲 / 孩子）")
    print("\n  演示入口：")
    print("   首页 → 对话（问头晕）→ 预问诊 → 科室匹配 → 医生 → 下单")
    print("   首页 → 健康 → 时间线（能看到刚才的订单与病历）")
    print("   首页 → 提醒（三位就诊人各一条，切人能看出按人分开）")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
