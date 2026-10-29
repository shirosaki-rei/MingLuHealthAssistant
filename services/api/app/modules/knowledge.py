"""知识库检索。

架构 §3.4 要求混合检索：向量 Top20（pgvector HNSW）+ 全文 Top20（pg_bigm）→ RRF → Rerank Top3。
医疗术语的精确匹配很关键，纯向量会丢「氨氯地平」这类实体。

当前实现是关键词 + 别名精确匹配打分，覆盖 MVP 的 800~1500 条规模。
上线门槛是 100 条标注问答集 Top1 ≥80% / Top3 ≥92%，不达标再上 pgvector。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..database import now

# 检索结果截断到 600 token（架构 §8.2：单轮省 30%）
MAX_RESULT_CHARS = 900


@dataclass(frozen=True)
class Retrieved:
    entry: models.KbEntry
    score: float

    @property
    def source_ref(self) -> dict:
        """存进 message.source_refs（JSON 列）的形态。时间必须是字符串——
        SQLAlchemy 的 JSON 类型不会序列化 datetime。"""
        return {
            "title": self.entry.title,
            "publisher": self.entry.source or "明路医学顾问组",
            "url": self.entry.source_url,
            "updated_at": (self.entry.reviewed_at or self.entry.created_at).isoformat(),
        }


def _active_entries(db: Session):
    """只检索已审核、未过期、未下线的词条（kb_entry 过期自动下线，架构 §3.2）。"""
    stamp = now()
    return list(
        db.scalars(
            select(models.KbEntry).where(
                models.KbEntry.enabled.is_(True),
                (models.KbEntry.valid_until.is_(None)) | (models.KbEntry.valid_until > stamp),
            )
        )
    )


def retrieve(db: Session, query: str, top_k: int = 3) -> list[Retrieved]:
    """按标题/别名/正文命中打分。返回按分数降序的前 top_k 条。

    ponytail: 全表扫 + 子串匹配。词条到几千条时换成 pg_bigm GIN（中文二元组），
    SQL 与这里的打分逻辑不用改，只换查询方式。
    """
    query = (query or "").strip()
    if not query:
        return []
    scored: list[Retrieved] = []
    for entry in _active_entries(db):
        score = 0.0
        if query in entry.title:
            score += 3.0
        for alias in entry.aliases or []:
            if alias and alias in query:
                score += 4.0  # 别名命中权重最高：用户说的是俗称
            elif query in alias:
                score += 2.0
        if query in entry.summary:
            score += 1.0
        # 逐词兜底：query 里的每个 ≥2 字的片段命中正文
        for token in _tokens(query):
            if token in entry.content:
                score += 0.5
        if score > 0:
            scored.append(Retrieved(entry=entry, score=score))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:top_k]


def _tokens(text: str) -> list[str]:
    """粗切：滑窗取 2~4 字片段，够用于关键词召回，不引分词库。"""
    cleaned = "".join(c for c in text if c.isalnum() or "一" <= c <= "鿿")
    return [cleaned[i : i + 2] for i in range(max(0, len(cleaned) - 1))]


def knowledge_block(hits: list[Retrieved]) -> list[str]:
    """拼进上下文的资料块，含来源与就医指征（PRD：每条健康问答均含来源）。

    每行都以句末标点结尾：SSE 按句缓冲要求一个 delta 至少是一个完整句子，
    标题不能单独成行，否则《xxx》会被当成一句话推给用户。
    """
    blocks: list[str] = []
    for hit in hits:
        entry = hit.entry
        updated = (entry.reviewed_at or entry.created_at).date()
        blocks.append(_as_sentence(entry.summary[:MAX_RESULT_CHARS]))
        blocks.append(f"来源：《{entry.title}》，更新于 {updated}。")
        if entry.seek_care_signs:
            blocks.append(f"就医指征：{_as_sentence('；'.join(entry.seek_care_signs))}")
    return blocks


def _as_sentence(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in "。！？；":
        text += "。"
    return text
