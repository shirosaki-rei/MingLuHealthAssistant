"""清掉某个演示账号的全部数据，再跑一遍 demo_seed.py。

`demo_seed.py` 按 relation 去重，账号里只要已经有一个 relation=self 的档案，它就只补剩下的。
所以「清掉重跑」必须是真清空，不然永远会剩下历史遗留的重复档案。

只删这个账号的行，不动科室/医生/知识库/红旗规则/预设角色（那些是 app/seed.py 的平台数据）。
**会先自动备份数据库**，备份文件名带时间戳，就在同目录。

用法：
    .venv\\Scripts\\python.exe scripts\\demo_reset.py 13800138000
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent
DB = API_ROOT / "minglu.db"

# 按 user_id 删的表。顺序无所谓（SQLite 默认不开外键约束），但 message 要先按
# conversation_id 删，它没有 user_id 列。
USER_TABLES = [
    "agent_memory", "ai_log", "consent_log", "consult_order", "conversation",
    "membership", "notify_log", "patient_profile", "pre_consult",
    "reminder", "report_doc", "share_export", "timeline_event",
]
# 归属列名不叫 user_id 的表（persona 用的是 owner_user_id）
OWNER_COL = {"persona": "owner_user_id"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    phone = sys.argv[1]

    if not DB.exists():
        print("找不到数据库：", DB)
        return 1

    bak = DB.with_name(f"{DB.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(DB, bak)
    print(f"已备份 → {bak.name}")

    con = sqlite3.connect(DB)
    try:
        row = con.execute("SELECT id FROM user WHERE phone = ?", (phone,)).fetchone()
        if not row:
            print(f"库里没有手机号 {phone} 的用户")
            return 1
        uid = row[0]

        conv_ids = [r[0] for r in con.execute(
            "SELECT id FROM conversation WHERE user_id = ?", (uid,))]
        if conv_ids:
            marks = ",".join("?" * len(conv_ids))
            n = con.execute(f"DELETE FROM message WHERE conversation_id IN ({marks})",
                            conv_ids).rowcount
            print(f"message -{n}")

        total = 0
        for table in USER_TABLES + list(OWNER_COL):
            col = OWNER_COL.get(table, "user_id")
            n = con.execute(f"DELETE FROM {table} WHERE {col} = ?", (uid,)).rowcount
            total += n
            if n:
                print(f"{table} -{n}")
        con.commit()
        print(f"合计删除 {total} 行（用户 {phone}）\n")
        sys.stdout.flush()  # 子进程会继承 stdout，不冲缓冲的话两边输出会串
    finally:
        con.close()

    print("重新跑 demo_seed…\n")
    env = {**__import__("os").environ, "DEMO_PHONE": phone}
    return subprocess.call([sys.executable, str(API_ROOT / "scripts" / "demo_seed.py")], env=env)


if __name__ == "__main__":
    sys.exit(main())
