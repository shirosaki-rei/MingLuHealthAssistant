"""补齐演示库里缺的医生和知识库。

`app/seed.py` 的 `seed()` 是「每张表空了才写」，所以种子列表扩容之后，**旧库不会更新**——
本地库是很早以前跑的，现在只剩 10 个医生、5 条知识库，12 个科室里 8 个点进去是空的。
这个脚本按 `app.seed` 里的列表补差额：只插缺失的行，不删不改已有的，可重复跑。

用法：
    .venv\\Scripts\\python.exe scripts\\seed_backfill.py
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app import models
from app.database import get_sessionmaker, now
from app.seed import DOCTORS, KB_ENTRIES


def main() -> int:
    db = get_sessionmaker()()
    try:
        have_docs = {(d.name, d.department_code) for d in db.scalars(select(models.Doctor))}
        have_kb = {k.title for k in db.scalars(select(models.KbEntry))}

        new_docs = [d for d in DOCTORS if (d[0], d[1]) not in have_docs]
        new_kb = [k for k in KB_ENTRIES if k[0] not in have_kb]

        if new_docs:
            db.add_all(
                models.Doctor(
                    name=name,
                    department_code=dept,
                    title=title,
                    hospital=hospital,
                    specialties=list(specialties),
                    sub_specialty=list(specialties),
                    price_cents=price,
                    avg_reply_hours=reply_hours,
                    rating=rating,
                    review_count=reviews,
                    online_status=online,
                    settle_ratio=0.7,
                    license_verified=True,
                    is_mock=True,
                )
                for name, dept, title, hospital, specialties, price, reply_hours, rating, reviews, online in new_docs
            )

        if new_kb:
            reviewed = now()
            db.add_all(
                models.KbEntry(
                    title=title,
                    aliases=list(aliases),
                    department_code=dept,
                    category="health",
                    summary=summary,
                    content=content,
                    seek_care_signs=list(signs),
                    source=source,
                    source_url=url,
                    reviewer="医学顾问组",
                    reviewer_license="（签约后填执业证书号）",
                    reviewed_at=reviewed,
                    valid_until=reviewed + timedelta(days=365),
                    version="1",
                    enabled=True,
                )
                for title, aliases, dept, summary, content, signs, source, url in new_kb
            )

        db.commit()
        print(f"医生 +{len(new_docs)}（现有 {len(have_docs) + len(new_docs)}/{len(DOCTORS)}）")
        print(f"知识库 +{len(new_kb)}（现有 {len(have_kb) + len(new_kb)}/{len(KB_ENTRIES)}）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
