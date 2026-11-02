"""平台层：模型调用、重试、降级、成本计量。

架构 §8.3：`llm_gateway` 只允许配置境内 base_url，白名单硬编码在 config.py，
运行时从环境变量注入任意 URL 会被直接拒绝——这条是数据不出境的硬约束。

降级（架构 §14）：模型不可用时返回 None，调用方改用知识库词条卡片 / 模板兜底，
**绝不给错误信息**。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
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
5. 医学内容（症状、指标、用药、检查）表述保持严谨中性；**其他内容不受此限**——
   问候、闲聊、情绪安抚可以充分展现角色性格，该温柔温柔，该活泼活泼
6. 涉及症状或指标时，给出该看哪个科室、什么情况要尽快就医
7. 身份声明只说一次：本次对话第一次回复、或给出具体就医/用药建议时，简单带一句自己是 AI；
   **不要每轮重复**（界面输入框上方有常驻声明，反复讲会显得像客服）

人设只影响语气与称呼，不影响以上任何一条。
8. 只用纯文本回答：不要用 markdown 语法（** 粗体、# 标题、- 列表、> 引用、表格都不要）。
   需要分点时用「1. 2. 3.」或分号，一句话说清一条。"""

DISCLAIMER = "以上内容由 AI 生成，仅供参考，不能替代医生面诊。"


class Provider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> LlmReply: ...

    def stream(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> Iterator[str]:
        """增量吐正文。一个 chunk 可能只有一两个字，调用方负责按句缓冲。"""
        ...


def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 一字一 token，其余约 4 字符一 token。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return cjk + max(1, (len(text) - cjk) // 4)


class LocalProvider:
    """无网络的确定性 provider。开发、测试、以及模型降级时都用它。

    输出按「查到的资料」组织，天然不触诊断/剂量/处方红线。
    """

    name = "local-template"
    model = "local-template"

    def complete(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> LlmReply:
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
                ),
            )
        )
        return LlmReply(
            text=answer,
            tokens_in=sum(estimate_tokens(m["content"]) for m in messages),
            tokens_out=estimate_tokens(answer),
            model=self.name,
        )

    def stream(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> Iterator[str]:
        # 本地模板没有增量概念，整篇当一块吐出去，调用方的按句缓冲逻辑不变
        yield self.complete(messages, max_tokens).text


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
        self.model = settings.llm_model or "default"
        self._effort = settings.llm_reasoning_effort
        # 读超时给到 90 秒：思考模型/排队时段的首 token 实测能到 20 秒以上，
        # 30 秒会在长 prompt 上随机 ReadTimeout，而那时降级出去用户只会看到模板兜底。
        self._client = httpx.Client(timeout=httpx.Timeout(90.0, connect=5.0))

    def complete(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> LlmReply:
        # max_tokens=None 时不传这个字段，用模型默认上限。思考模型的推理长度不可预测
        # （实测同一条输入推理从几百到四千 token 波动），写死额度会把正文挤成空 content。
        body: dict = {"model": self.model, "messages": messages, "stream": False}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if self._effort:
            body["reasoning_effort"] = self._effort
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}
        # 思考模型（deepseek-r1 / glm-z1 / qwq）把推理放在 reasoning_content，正文仍在 content。
        # 这里**只取 content**：思考过程不该给用户看，也不该进 SSE 的按句切分。
        message = payload["choices"][0]["message"]
        text = (message.get("content") or "").strip()
        if not text:
            # 思考模型把 max_tokens 全花在推理上时 content 会是空的。返回空文本会让用户
            # 看到空白气泡，宁可抛出去走降级（知识库模板兜底），那至少是句人话。
            raise ValueError(
                f"模型 {self.model!r} 返回了空 content"
                "（思考模型先把预算用在推理上，把 max_tokens 调大再试）"
            )
        return LlmReply(
            text=text,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=payload.get("model", self.model),
        )

    def stream(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> Iterator[str]:
        body: dict = {"model": self.model, "messages": messages, "stream": True}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if self._effort:
            body["reasoning_effort"] = self._effort
        # 请求在第一次迭代时才真正发出，所以调用方（编排层）不会被这里的异常打到
        with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json=body,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                # 只取 content：reasoning_content 是思考过程，不该给用户看；
                # 而且思考链里满是句号，混进来会把「按句切分」切得稀碎。
                text = delta.get("content")
                if text:
                    yield text


_provider: Provider | None = None
_provider_failed = False


def get_provider() -> Provider | None:
    """拿不到可用 provider 时返回 None，调用方走降级路径。

    每次决策都打 info 日志：配错 base_url（不在白名单、缺 key）时**不会报错**，
    只会静默降级成模板回复，用户看到知识库卡片还以为模型生效了。
    所以「当前用哪个 provider」必须能在启动日志里一眼看到。
    """
    global _provider, _provider_failed
    if _provider is not None or _provider_failed:
        return _provider
    if not get_settings().llm_base_url:
        _provider = LocalProvider()
        log.info("llm provider = local-template（未配 LLM_BASE_URL，回复走知识库模板兜底）")
        return _provider
    try:
        _provider = OpenAICompatProvider()
    except Exception as exc:  # noqa: BLE001 - 白名单拒绝或配置缺失都降级
        log.warning("llm provider 不可用，降级到模板：%s", exc)
        _provider_failed = True
        return None
    log.info("llm provider = openai-compat，model = %s", get_settings().llm_model or "default")
    return _provider


def debug_bundle(bundle: ContextBundle) -> str:
    return json.dumps({"messages": bundle.to_messages()}, ensure_ascii=False, default=str)[:2000]
