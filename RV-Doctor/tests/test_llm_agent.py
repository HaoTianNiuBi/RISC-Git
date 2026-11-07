"""llm_agent 单元测试（M5，全离线：transport 注入 fake，不打真实 API）。

对应 docs/05 M5 DoD 与 docs/06 §7：
- 未配置降级 / 请求失败降级 / 输出不可解析 / Schema 校验逐条兜底；
- 提示词纪律（禁编造数字）与出口防线（_sanitize）。
"""
from __future__ import annotations

import json

import pytest

from optimizer.llm_agent import (
    LlmConfig,
    _CLAIM_RE,
    build_prompt,
    suggest,
)

VALID_ITEM = {
    "title": "将循环边界计算移出循环",
    "location": "main 的 for 循环",
    "explanation": "循环不变量外提，引用实测动态指令数",
    "modified_source": "int main(void){ int sum = 100*101/2; printf(\"sum=%d\\n\", sum); return 0; }",
    "expected_gain": "medium",
    "risk": "low",
    "confidence": 0.87,
}


def _cfg(**kw) -> LlmConfig:
    base = dict(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")
    base.update(kw)
    return LlmConfig(**base)


def _transport_returning(text: str):
    def fake(cfg, messages):
        assert cfg.model == "test-model"
        assert messages[0]["role"] == "system"
        return text
    return fake


def _transport_failing(exc: Exception):
    def fake(cfg, messages):
        raise exc
    return fake


def _metrics_fixture() -> dict:
    return {
        "meta": {"arch": "rv64i", "opt": "O0"},
        "static": {"total_instructions": 210,
                    "functions": [{"name": "main", "instructions": 120}]},
        "dynamic": {"total": 118000, "by_class": {"integer": 51000, "memory": 32000},
                     "calls": 10000},
        "energy": {"score": 231500},
    }


def test_not_configured_degrades(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    outcome = suggest(src, _metrics_fixture(), [], cfg=LlmConfig(), transport=_transport_returning("[]"))
    assert outcome.suggestions == []
    assert "规则引擎模式" in outcome.degraded_reason


def test_transport_failure_degrades(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    outcome = suggest(src, _metrics_fixture(), [], cfg=_cfg(),
                       transport=_transport_failing(TimeoutError("boom")))
    assert outcome.suggestions == []
    assert "TimeoutError" in outcome.degraded_reason
    assert outcome.degraded_reason  # 降级原因必须可见（NFR5）


def test_unparseable_output_rejected(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    outcome = suggest(src, _metrics_fixture(), [], cfg=_cfg(),
                       transport=_transport_returning("我觉得这段代码还不错，没有数组越界。"))
    assert outcome.suggestions == []
    assert "未找到 JSON 数组" in outcome.degraded_reason
    assert outcome.rejected and "未找到" in outcome.rejected[0]["reason"]


def test_valid_suggestions_kept(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    payload = json.dumps([VALID_ITEM], ensure_ascii=False)
    outcome = suggest(src, _metrics_fixture(), [], cfg=_cfg(),
                       transport=_transport_returning(payload))
    assert outcome.degraded_reason == ""
    assert len(outcome.suggestions) == 1
    assert outcome.suggestions[0]["title"] == "将循环边界计算移出循环"
    assert outcome.suggestions[0]["confidence"] == pytest.approx(0.87)


def test_markdown_fence_tolerated(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    payload = "```json\n" + json.dumps([VALID_ITEM], ensure_ascii=False) + "\n```"
    outcome = suggest(src, _metrics_fixture(), [], cfg=_cfg(),
                       transport=_transport_returning(payload))
    assert len(outcome.suggestions) == 1


def test_partial_schema_reject_keeps_valid(tmp_path):
    bad = dict(VALID_ITEM, confidence=1.7)  # 超出 [0,1]
    payload = json.dumps([VALID_ITEM, bad], ensure_ascii=False)
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    outcome = suggest(src, _metrics_fixture(), [], cfg=_cfg(),
                       transport=_transport_returning(payload))
    assert len(outcome.suggestions) == 1
    assert len(outcome.rejected) == 1
    assert "confidence" in outcome.rejected[0]["reason"]


def test_prompt_carries_measured_numbers_and_ban():
    messages = build_prompt("int main(void){return 0;}", _metrics_fixture(), [])
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert "118000" in user                       # 实测数字进入上下文
    assert "不得自行估算" in system or "禁止" in system  # 数字禁令明确
    assert "规则引擎发现" in user or "未发现" in user


def test_sanitize_strips_fabricated_claim_in_source():
    assert _CLAIM_RE.search("/* 优化后指令减少 30000 条 */")
    src_text = "int main(void){return 0;} /* 优化后指令减少 30000 条 */"
    import re as _re
    cleaned = _CLAIM_RE.sub("X", src_text)
    assert "30000" not in cleaned


def test_whitespace_config_degrades(tmp_path):
    """空白字段视为未配置（界面粘贴带空格的 Key 不至于发出残缺请求）。"""
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")
    outcome = suggest(src, _metrics_fixture(), [],
                      cfg=_cfg(api_key="   "), transport=_transport_returning("[]"))
    assert outcome.suggestions == []
    assert "规则引擎模式" in outcome.degraded_reason
