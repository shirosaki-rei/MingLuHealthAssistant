"""真流式的增量切分回归。

守三件事：chunk 切在句子中间时不能提前吐半句、收尾时要把最后半句吐出来、
断流时不能安静结束（用户会看到空白气泡）。
"""
from __future__ import annotations

import pytest

from app.modules import safety
from app.modules.chat import _Plan, _sentences


def _plan(stream=None, reply: str = "") -> _Plan:
    return _Plan(
        reply=reply,
        sources=[],
        redflag=None,
        injected=None,
        intent="health_qa",
        model="test",
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        stream=stream,
    )


def test_chunks_split_mid_sentence_are_buffered():
    def gen():
        # 每块只有一两个字，且刻意断在句子中间
        for piece in "我今天有点累。要不要休息一下？":
            yield piece

    assert list(_sentences(_plan(gen()))) == ["我今天有点累。", "要不要休息一下？"]


def test_stream_path_matches_whole_text_path():
    text = "第一句。第二句！第三句？"
    streamed = list(_sentences(_plan(iter(text))))
    whole = list(_sentences(_plan(reply=text)))
    assert streamed == whole == ["第一句。", "第二句！", "第三句？"]


def test_broken_stream_yields_a_spoken_sentence():
    def gen():
        yield "先说半句"
        raise ConnectionError("对方关了连接")

    out = list(_sentences(_plan(gen())))
    assert len(out) == 1
    assert out[0] != "", "断流时必须补一句人话，不能静默"
    # 兜底句也得过后置校验，否则它自己就发不出去
    assert safety.postcheck(out[0]).ok, out[0]


def test_plain_text_path_still_works():
    assert list(_sentences(_plan(reply="只有一句。"))) == ["只有一句。"]
