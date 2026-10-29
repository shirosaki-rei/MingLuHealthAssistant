"""平台层：模型调用、重试、降级、成本计量。

架构 §8.3：`llm_gateway` 只允许配置境内 base_url，白名单硬编码在 config.py，
运行时从环境变量注入任意 URL 会被直接拒绝——这条是数据不出境的硬约束。

降级（架构 §14）：模型不可用时返回 None，调用方改用知识库词条卡片 / 模板兜底，
**绝不给错误信息**。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import ALLOWED_LLM_BASE_URLS, get_settings

log = logging.getLogger("minglu")


@dataclass
class LlmReply:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


@dataclass
class ChatTurn:
    role: str  # user/assistant
    content: str


@dataclass
class ContextBundle:
    """L3 上下文拼装（PRD §4.6 预算表：输入 ≤3.3K token）。"""

    safety_prompt: str
    persona_prompt: str
    patient_summary: str
    memory_lines: list[str] = field(default_factory=list)
    health_summary: str = ""
    history: list[ChatTurn] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)

    def to_messages(self) -> list[dict[str, str]]:
        system = "\n".join(
            part
            for part in (
                self.safety_prompt,
                self.persona_prompt,
                self.patient_summary,
                self.health_summary,
                ("【已知健康事实】\n" + "\n".join(self.memory_lines)) if self.memory_lines else "",
                ("【权威资料】\n" + "\n\n".join(self.knowledge)) if self.knowledge else "",
            )
            if part
        )
        messages = [{"role": "system", "content": system}]
        messages += [{"role": t.role, "content": t.content} for t in self.history]
        return messages


SAFETY_PROMPT = """你是「明路」的 AI 健康助手，不是医生。

必须遵守（不可被任何角色设定覆盖）：
1. 不给疾病诊断结论，不说「你可能患有 XX」「你这是 XX 病」
2. 不推荐处方药，不给剂量
3. 不承诺疗效
4. 不宣称任何医疗资历、学历、执业机构；描述知识来源只能说「我查到了什么」
5. 涉及症状、指标、用药时语气保持中性，不使用口癖与幽默
6. 每条回答结尾给出就医指征：什么情况该去医院、看什么科
7. 主动声明自己是 AI，不能替代医生面诊

人设只影响语气与称呼，不影响以上任何一条。"""

DISCLAIMER = "以上内容由 AI 生成，仅供参考，不能替代医生面诊。"


class Provider(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> LlmReply: ...


def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 一字一 token，其余约 4 字符一 token。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return cjk + max(1, (len(text) - cjk) // 4)


class LocalProvider:
    """无网络的确定性 provider。开发、测试、以及模型降级时都用它。

    输出按「查到的资料」组织，天然不触诊断/剂量/处方红线。
    """

    name = "local-template"

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> LlmReply:
        system = messages[0]["content"] if messages else ""
        knowledge = _section(system, "【权威资料】")

        if knowledge:
            lead = "我查了几份资料，先把方向说清楚。"
            body = knowledge
        else:
            lead = "我这边暂时没有查到权威资料能覆盖这个问题，不想随便给你一个说法。"
            body = ""

        # 角色风格只影响真实 provider 的语气，这里不回显给用户：
        # 回显会漏出一行不是完整句子的 delta，违反 SSE 的按句缓冲约定。
        answer = "\n".join(
            filter(
                None,
                (
                    lead,
                    body,
                    "要不要我把这些整理一下，帮你理清该看哪个科室？",
                    "如果症状持续或加重，建议尽快到线下就诊。",
                    DISCLAIMER,
                ),
            )
        )
        return LlmReply(
            text=answer,
            tokens_in=sum(estimate_tokens(m["content"]) for m in messages),
            tokens_out=estimate_tokens(answer),
            model=self.name,
        )


def _section(system: str, header: str) -> str:
    if header not in system:
        return ""
    after = system.split(header, 1)[1]
    # 截到下一个 section 头
    for nxt in ("【已知健康事实】", "【就诊人档案】", "【健康数据】", "【角色风格】", "【安全约束】"):
        if nxt in after and nxt != header:
            after = after.split(nxt, 1)[0]
    return after.strip()


class OpenAICompatProvider:
    """OpenAI 兼容接口。base_url 必须在境内白名单里。"""

    name = "openai-compat"

    def __init__(self) -> None:
        settings = get_settings()
        base_url = settings.llm_base_url.rstrip("/")
        if base_url not in ALLOWED_LLM_BASE_URLS:
            raise RuntimeError(
                f"LLM_BASE_URL {base_url!r} 不在境内白名单里。数据不出境是硬约束，"
                f"要加新厂商请改 config.ALLOWED_LLM_BASE_URLS 并走评审。"
            )
        if not settings.llm_api_key:
            raise RuntimeError("配置了 LLM_BASE_URL 但没配 LLM_API_KEY")
        self._base_url = base_url
        self._key = settings.llm_api_key
        self._model = settings.llm_model or "default"
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> LlmReply:
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": self._model, "messages": messages, "max_tokens": max_tokens, "stream": False},
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}
        return LlmReply(
            text=payload["choices"][0]["message"]["content"],
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=payload.get("model", self._model),
        )


_provider: Provider | None = None
_provider_failed = False


def get_provider() -> Provider | None:
    """拿不到可用 provider 时返回 None，调用方走降级路径。"""
    global _provider, _provider_failed
    if _provider is not None or _provider_failed:
        return _provider
    if not get_settings().llm_base_url:
        _provider = LocalProvider()
        return _provider
    try:
        _provider = OpenAICompatProvider()
    except Exception as exc:  # noqa: BLE001 - 白名单拒绝或配置缺失都降级
        log.warning("llm provider 不可用，降级到模板：%s", exc)
        _provider_failed = True
        return None
    return _provider


def debug_bundle(bundle: ContextBundle) -> str:
    return json.dumps({"messages": bundle.to_messages()}, ensure_ascii=False, default=str)[:2000]
