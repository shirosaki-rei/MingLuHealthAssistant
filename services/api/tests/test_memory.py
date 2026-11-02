"""记忆抽取回归：LLM 总结式（第二期）替代关键词匹配。

这三条守的是：只抽明确说出口的、key 必须落在固定分类里、模型输出带垃圾时要能抠出 JSON。
"""
from __future__ import annotations

import json

from app.modules.chat import MEMORY_KEYS, _parse_facts


def test_parses_clean_json():
    facts = _parse_facts('{"facts":[{"key":"medication","value":"每天早晨服用氨氯地平 5mg"}]}')
    assert facts == [("medication", "每天早晨服用氨氯地平 5mg")]


def test_parses_json_wrapped_in_markdown_and_prose():
    """模型爱套 ```json 代码块、也爱先解释一句。掐头去尾找括号都要能抠出来。"""
    raw = '好的，我来抽取：\n```json\n{"facts":[{"key":"symptom","value":"近两周入睡困难"}]}\n```'
    assert _parse_facts(raw) == [("symptom", "近两周入睡困难")]


def test_drops_unknown_keys_and_empty_values():
    """key 不在固定分类里就丢：remember() 按 (user, patient, key) 去重，
    自由 key 会无限膨胀、同义不同名，分类表外的等于抽了也记不住。"""
    raw = json.dumps(
        {
            "facts": [
                {"key": "在吃的药", "value": "氨氯地平"},   # 中文 key，不在表里
                {"key": "medication", "value": "  "},       # 空 value
                {"key": "habit", "value": "每天晚饭后快走 30 分钟"},
            ]
        },
        ensure_ascii=False,
    )
    assert _parse_facts(raw) == [("habit", "每天晚饭后快走 30 分钟")]


def test_no_health_info_returns_empty():
    assert _parse_facts('{"facts":[]}') == []
    assert _parse_facts("今天天气不错") == []


def test_key_table_is_fixed():
    """分类表是契约：加了新分类要同步 MEMORY_PROMPT 里的说明，别只改一边。"""
    assert "medication" in MEMORY_KEYS and "allergy" in MEMORY_KEYS
    assert len(MEMORY_KEYS) == 8
