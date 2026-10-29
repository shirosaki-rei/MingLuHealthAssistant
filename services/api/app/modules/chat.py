"""对话编排：L0 四道防线在这里串起来（架构 §5.1）。

顺序是硬约束，不能调换：

  ① 注入检测（<50ms，命中即拒答并记日志）
  ② 红旗检测（<20ms，纯规则）—— **放在意图识别之前**
  ③ 意图识别（求诊断 → 引导预问诊；健康问答 → 检索）
  ④ 逐句后置校验 → 通过才推给客户端（按句缓冲，用户永远看不到未校验的半句）
  ⑤ 落 ai_log（掩码后）

为什么红旗必须在意图识别之前：否则「我胸痛，挂什么科」会先进科室匹配，
延迟急救引导。这是 PRD 评审里发现的问题。
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import new_id, now
from ..deps import CurrentUser, DbDep, PageQuery, paginate, require_action
from ..errors import request_id_var
from ..modules import knowledge, safety
from ..modules.memory import remember, recent_memories
from ..modules.profile import get_owned_patient
from ..platform import llm

log = logging.getLogger("minglu")
router = APIRouter(tags=["chat"])

RECENT_TURNS = 4  # 最近 4 轮对话注入上下文（PRD §4.6 预算 1200t）


@dataclass
class SseEvent:
    event: str
    data: dict

    def render(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


@router.post("/v1/chat/stream")
def chat_stream(
    payload: schemas.ChatRequest,
    *,
    db: DbDep,
    user: CurrentUser,
    _: None = Depends(require_action("chat.stream")),
) -> StreamingResponse:
    """SSE 流。见契约：delta / redflag / done 三种事件，一个 delta 至少一个完整句子。"""
    patient = get_owned_patient(db, user.id, payload.patient_id)
    conversation = _resolve_conversation(db, user, payload)
    persona = _resolve_persona(db, user.id, payload.persona_id)

    # 用户消息先落库：即使后面红旗中断、模型挂了，这句也在
    user_message = models.Message(
        id=new_id(),
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        created_at=now(),
    )
    db.add(user_message)
    db.commit()  # SSE 长连接里不能再靠请求结束时的隐式提交

    plan = _orchestrate(db, user, patient, conversation, persona, user_message)

    return StreamingResponse(
        _stream(db, user, conversation, user_message, plan),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx 关缓冲，否则 SSE 被攒着不推
            "X-Request-Id": request_id_var.get(),
        },
    )


@dataclass
class _Plan:
    """①~③ 的结果。生成放在流里做，红旗与注入在这里就短路。"""

    reply: str
    sources: list[dict]
    redflag: safety.RedFlagHit | None
    injected: str | None
    intent: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


def _orchestrate(
    db: Session,
    user: models.User,
    patient: models.PatientProfile,
    conversation: models.Conversation,
    persona: models.Persona | None,
    user_message: models.Message,
) -> _Plan:
    started = time.monotonic()
    text = user_message.content

    # ① 注入检测。宁可误拦（PRD §14 #3）
    injected = safety.detect_injection(text)
    if injected:
        return _Plan(
            reply=safety.INJECTION_REFUSAL,
            sources=[],
            redflag=None,
            injected=injected,
            intent="injection",
            model="rules",
            tokens_in=0,
            tokens_out=0,
            latency_ms=_ms(started),
        )

    # ② 红旗检测（意图识别之前）
    hit = safety.detect_redflag(text)
    if hit:
        return _Plan(
            reply=hit.message,
            sources=[],
            redflag=hit,
            injected=None,
            intent="redflag",
            model="rules",
            tokens_in=0,
            tokens_out=0,
            latency_ms=_ms(started),
        )

    # ③ 意图识别：求诊断 → 不答结论，引导预问诊
    if _wants_diagnosis(text):
        return _Plan(
            reply=(
                "我没法给你下诊断，也不想猜。不如我按问诊的思路帮你把情况问清楚，"
                "再告诉你看哪个科室更合适。要开始吗？"
            ),
            sources=[],
            redflag=None,
            injected=None,
            intent="preconsult_handoff",
            model="rules",
            tokens_in=0,
            tokens_out=0,
            latency_ms=_ms(started),
        )

    # 健康问答：检索 → 生成
    hits = knowledge.retrieve(db, text, top_k=3)
    bundle = _build_context(db, user, patient, conversation, persona, hits)
    provider = llm.get_provider()
    if provider is None:
        # 模型全挂：降级为只给检索到的资料（架构 §7.2 降级策略）
        reply = "\n\n".join(knowledge.knowledge_block(hits)) or (
            "我暂时没法整理答案。可以先看看相关知识，或直接找医生。"
        )
        return _Plan(
            reply=reply + f"\n\n{llm.DISCLAIMER}",
            sources=[h.source_ref for h in hits],
            redflag=None,
            injected=None,
            intent="degraded",
            model="degraded",
            tokens_in=0,
            tokens_out=0,
            latency_ms=_ms(started),
        )

    completion = provider.complete(bundle.to_messages(), max_tokens=800)
    return _Plan(
        reply=completion.text,
        sources=[h.source_ref for h in hits],
        redflag=None,
        injected=None,
        intent="health_qa",
        model=completion.model,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        latency_ms=_ms(started),
    )


def _stream(
    db: Session,
    user: models.User,
    conversation: models.Conversation,
    user_message: models.Message,
    plan: _Plan,
) -> Iterator[str]:
    """④ 逐句后置校验后推送。任一句不通过就中断该句并收口。"""
    assistant = models.Message(
        id=new_id(),
        conversation_id=conversation.id,
        role="assistant",
        content="",
        source_refs=plan.sources or None,
        redflag_level=plan.redflag.level if plan.redflag else "none",
        created_at=now(),
    )
    db.add(assistant)
    db.commit()

    if plan.redflag:
        # 红旗：先推 redflag，再推 done，不继续生成。角色语气在此场景全部失效。
        yield SseEvent(
            "redflag",
            {
                "level": plan.redflag.level,
                "message": plan.redflag.message,
                "action": plan.redflag.action,
                "rule_id": plan.redflag.rule_id,
            },
        ).render()
        assistant.content = plan.redflag.message
        db.commit()
        yield _done(db, user, conversation, assistant, plan).render()
        return

    accepted: list[str] = []
    blocked = False
    for sentence in safety.iter_sentences(plan.reply)[0]:
        check = safety.postcheck(sentence)
        if not check.ok:
            # 命中禁用词：不发这句，改发固定收口
            blocked = True
            accepted.append("这部分我没法展开说，建议线下面诊时问医生。")
            break
        accepted.append(sentence)
        yield SseEvent("delta", {"delta": sentence}).render()

    assistant.content = "".join(accepted) or llm.DISCLAIMER
    _extract_memories(db, user, conversation, user_message, assistant)
    db.commit()
    yield _done(db, user, conversation, assistant, plan).render()


def _done(
    db: Session,
    user: models.User,
    conversation: models.Conversation,
    assistant: models.Message,
    plan: _Plan,
) -> SseEvent:
    """⑤ 落 ai_log（掩码后），request_id 与响应头对齐。"""
    patient_names = [p.name for p in db.scalars(select(models.PatientProfile).where(models.PatientProfile.user_id == user.id))]
    db.add(
        models.AiLog(
            request_id=request_id_var.get(),
            user_id=user.id,
            scene="chat",
            conversation_id=conversation.id,
            input_masked=safety.mask_pii(_user_text(db, conversation), patient_names),
            output_masked=safety.mask_pii(assistant.content, patient_names),
            model=plan.model,
            retrieval_ids=[s.get("title") for s in plan.sources],
            tokens_in=plan.tokens_in,
            tokens_out=plan.tokens_out,
            latency_ms=plan.latency_ms,
            injected_flag=plan.injected is not None,
            blocked_flag=plan.injected is not None or plan.redflag is not None,
            block_reason=plan.injected or (plan.redflag.rule_id if plan.redflag else None),
            redflag_rule_id=plan.redflag.rule_id if plan.redflag else None,
            created_at=now(),
        )
    )
    db.commit()
    return SseEvent(
        "done",
        {
            "request_id": request_id_var.get(),
            "conversation_id": conversation.id,
            "message_id": assistant.id,
            "usage": {"in": plan.tokens_in, "out": plan.tokens_out},
        },
    )


def _user_text(db: Session, conversation: models.Conversation) -> str:
    last = db.scalars(
        select(models.Message)
        .where(models.Message.conversation_id == conversation.id, models.Message.role == "user")
        .order_by(models.Message.created_at.desc())
        .limit(1)
    ).first()
    return last.content if last else ""


# ---------------------------------------------------------------- 上下文拼装


def _build_context(
    db: Session,
    user: models.User,
    patient: models.PatientProfile,
    conversation: models.Conversation,
    persona: models.Persona | None,
    hits: list[knowledge.Retrieved],
) -> llm.ContextBundle:
    """PRD §4.6 预算表：输入合计 ≤3.3K token。"""
    memories = recent_memories(db, user.id, patient.id, limit=10)
    history = list(
        db.scalars(
            select(models.Message)
            .where(models.Message.conversation_id == conversation.id)
            .order_by(models.Message.created_at.desc())
            .limit(RECENT_TURNS * 2)
        )
    )[::-1]

    return llm.ContextBundle(
        safety_prompt=llm.SAFETY_PROMPT,
        persona_prompt=_persona_prompt(persona),
        patient_summary=_patient_summary(patient),
        memory_lines=[f"{m.key}：{m.value}" for m in memories],
        health_summary=_health_summary(db, patient.id),
        history=[llm.ChatTurn(role=m.role, content=m.content) for m in history],
        knowledge=knowledge.knowledge_block(hits),
    )


def _persona_prompt(persona: models.Persona | None) -> str:
    """L2a 人格层：只影响语气与称呼，不影响医学立场（PRD §4.1）。"""
    if persona is None:
        return "【角色风格】温和、简洁"
    tones = "、".join(persona.tones or []) or "温和"
    parts = [
        f"【角色风格】自称「{persona.self_ref or '我'}」，语气{tones}",
        f"形象：{persona.species}",
    ]
    if persona.catchphrase:
        parts.append(f"口癖：{persona.catchphrase}（涉及症状、指标、用药时不要出现）")
    if persona.worldview:
        parts.append(f"背景设定：{persona.worldview}（纯风格，不构成任何专业能力）")
    parts.append("以上只影响表达方式，不影响安全约束。")
    return "\n".join(parts)


def _patient_summary(patient: models.PatientProfile) -> str:
    bits = [f"姓名 {patient.name}（{patient.relation}）", f"性别 {patient.gender}"]
    if patient.birth_date:
        bits.append(f"出生 {patient.birth_date}")
    if patient.chronic_conditions:
        bits.append("慢性病 " + "、".join(patient.chronic_conditions))
    if patient.allergy_notes:
        bits.append(f"过敏 {patient.allergy_notes}")
    return "【就诊人档案】" + "；".join(bits)


def _health_summary(db: Session, patient_id: str) -> str:
    rows = list(
        db.scalars(
            select(models.HealthRecord)
            .where(models.HealthRecord.patient_id == patient_id)
            .order_by(models.HealthRecord.measured_at.desc())
            .limit(5)
        )
    )
    if not rows:
        return ""
    return "【健康数据】" + "；".join(
        f"{r.type} {r.value}{r.unit or ''}（{r.measured_at.date()}）" for r in rows
    )


def _resolve_conversation(
    db: Session, user: models.User, payload: schemas.ChatRequest
) -> models.Conversation:
    if payload.conversation_id:
        conversation = db.get(models.Conversation, payload.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            from ..errors import not_found

            raise not_found("会话")
        return conversation
    conversation = models.Conversation(
        id=new_id(),
        user_id=user.id,
        patient_id=payload.patient_id,
        persona_id=payload.persona_id,
        scene="chat",
        created_at=now(),
    )
    db.add(conversation)
    db.flush()
    return conversation


def _resolve_persona(
    db: Session, user_id: str, persona_id: str | None
) -> models.Persona | None:
    if not persona_id:
        return None
    persona = db.get(models.Persona, persona_id)
    if persona is None:
        return None
    if not persona.is_preset and persona.owner_user_id != user_id:
        return None
    return persona


def _wants_diagnosis(text: str) -> bool:
    """求诊断类问题不输出结论，引导率 100%（PRD 验收）。"""
    markers = ("我是不是", "是什么病", "啥病", "严重吗", "得了什么", "是不是癌", "确诊", "什么原因导致")
    return any(m in text for m in markers)


def _extract_memories(
    db: Session,
    user: models.User,
    conversation: models.Conversation,
    user_message: models.Message,
    assistant: models.Message,
) -> None:
    """从用户原话里抽事实型记忆。只认明确的关键词，不猜。

    ponytail: 关键词抽取。上小模型抽取前，抽错的代价是用户看到「我记一下……对吗」
    确认卡，误抽由确认环节兜住。
    """
    patient_id = conversation.patient_id
    text = user_message.content
    for keyword, key in (("过敏", "allergy"), ("高血压", "chronic_hypertension"), ("糖尿病", "chronic_diabetes")):
        if keyword in text:
            remember(
                db,
                user_id=user.id,
                patient_id=patient_id,
                kind="fact",
                key=key,
                value=text[:200],
                source_msg_id=user_message.id,
                confirmed=False,
            )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


@router.get("/v1/chat/history", response_model=schemas.ChatHistory)
def chat_history(
    *,
    db: DbDep,
    user: CurrentUser,
    patient_id: str | None = None,
    conversation_id: str | None = None,
    page: PageQuery = Depends(),
    _: None = Depends(require_action("chat.history")),
) -> schemas.ChatHistory:
    """至少传 patient_id 或 conversation_id 之一，否则不知道该查谁。"""
    if not patient_id and not conversation_id:
        from ..errors import ApiError, Code

        raise ApiError(Code.VALIDATION_ERROR, "需要传 patient_id 或 conversation_id")

    stmt = (
        select(models.Message)
        .join(models.Conversation, models.Message.conversation_id == models.Conversation.id)
        .where(models.Conversation.user_id == user.id)
    )
    if conversation_id:
        stmt = stmt.where(models.Conversation.id == conversation_id)
    else:
        stmt = stmt.where(models.Conversation.patient_id == patient_id)
    stmt = stmt.order_by(models.Message.created_at.desc())

    items, total, has_more = paginate(db, stmt, page)
    return schemas.ChatHistory(
        items=[
            schemas.ChatMessageOut(
                id=m.id,
                role=m.role,  # type: ignore[arg-type]
                content=m.content,
                source_refs=[schemas.SourceRef.model_validate(s) for s in (m.source_refs or [])] or None,
                redflag_level=m.redflag_level,  # type: ignore[arg-type]
                created_at=m.created_at,
            )
            for m in items
        ],
        total=total,
        has_more=has_more,
    )
