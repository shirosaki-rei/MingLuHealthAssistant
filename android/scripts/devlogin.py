"""真机登录：跑一遍登录页，账号和验证码都不打印。

配合 `devtap.py` 用。BASE_URL 从 `android/api/http.ts` 里读，不硬编码——
那行改了就自动跟上，不会出现脚本连旧 IP、App 连新地址的事。

    python devlogin.py [手机号]

坐标是 vivo V2520A（1080x2376）上量的，换设备要重新量（用 devtap.py find）。
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTTP_TS = ROOT / "android" / "api" / "http.ts"
DB = ROOT / "services" / "api" / "minglu.db"
DEV = "10CG1Y1XAT002HD"

PHONE_IN = (400, 780)
CODE_IN = (300, 1020)
SMS_BTN = (480, 1180)
AGREE_BOX = (110, 1470)
LOGIN_BTN = (539, 1650)


def sh(*a: str) -> str:
    return subprocess.run(a, capture_output=True, text=True, timeout=90).stdout.strip()


def tap(xy: tuple[int, int]) -> None:
    sh("adb", "-s", DEV, "shell", "input", "tap", str(xy[0]), str(xy[1]))


def text(s: str) -> None:
    sh("adb", "-s", DEV, "shell", "input", "text", s)


def base_url() -> str:
    m = re.search(r"BASE_URL\s*=\s*'([^']+)'", HTTP_TS.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"没在 {HTTP_TS} 里找到 BASE_URL")
    return m.group(1)


def post(url: str, body: str) -> int:
    req = urllib.request.Request(
        url, data=body.encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def main() -> None:
    base = base_url()
    print(f"BASE_URL = {base}")

    con = sqlite3.connect(DB)
    phone = (
        sys.argv[1]
        if len(sys.argv) > 1
        else con.execute("select phone from user where status='active' order by created_at limit 1").fetchone()[0]
    )
    print(f"账号长度 {len(phone)} 位")

    print("发验证码:", post(f"{base}/v1/auth/sms", '{"phone":"%s"}' % phone))

    tap(AGREE_BOX)
    time.sleep(0.6)
    tap(PHONE_IN)
    time.sleep(0.8)
    text(phone)
    time.sleep(0.5)

    tap(SMS_BTN)
    time.sleep(3)
    row = con.execute("select code from sms_code where phone=?", (phone,)).fetchone()
    if not row:
        sys.exit("sms_code 里没记录（可能被 60 秒频控挡了），等一分钟再跑")
    print(f"取到验证码（{len(row[0])} 位，未打印）")

    tap(CODE_IN)
    time.sleep(0.8)
    text(row[0])
    time.sleep(0.5)

    tap(LOGIN_BTN)
    time.sleep(5)
    print("登录完成")


if __name__ == "__main__":
    main()
