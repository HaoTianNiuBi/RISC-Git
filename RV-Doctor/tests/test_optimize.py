"""run_optimize 桥接层测试（docs/11 D1 接线）。

- 离线 glue 测试：monkeypatch 规则/LLM/验证器，验证任务目录证据读取、
  候选源码落盘、baseline 组装与结果结构（不打工具链、不打网络）；
- 集成测试：真实 GCC + QEMU 走 run_checkup → run_optimize（LLM 注入
  离线候选），验证闭环 ACCEPTED（工具链缺失自动 skip，与 test_verifier
  同策略）。全程不发起真实 LLM API 调用。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline
from optimizer.llm_agent import LlmConfig, SuggestOutcome
from tests.conftest import requires_qemu
from tests.toolchain import HELLO_C, requires_gcc


# ---------------------------------------------------------------------------
# 共享样例
# ---------------------------------------------------------------------------

FINDING = {
    "id": "r1-1",
    "rule_id": "R1",
    "title": "循环不变量函数调用",
    "location": {"function": "main", "scope": "user"},
    "evidence": {"call_exec": 5},
    "suggestion": "将循环不变量调用外提",
    "risk": "low",
    "confidence": 0.8,
}

SUGGESTION = {
    "title": "外提循环不变量",
    "explanation": "strlen 结果与循环变量无关，外提后循环体内少跑一次调用",
    "modified_source": "int optimized(void){return 1;}\n",
    "expected_gain": "medium",
    "risk": "low",
    "confidence": 0.9,
}


def _seed_task_dir(workdir: Path) -> None:
    """伪造一个 run_checkup 完成后的任务目录（仅桥接层读取的文件）。"""
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "app.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    (workdir / "stdout.txt").write_text("sum=5050\n", encoding="utf-8")
    (workdir / "disasm.txt").write_text("dummy\n", encoding="utf-8")
    (workdir / "trace.log").write_text("dummy\n", encoding="utf-8")
    metrics = {
        "meta": {"task_id": "t-test", "arch": "rv64gc", "opt": "O0",
                 "toolchain": "xPack GCC 15.2.0", "qemu": "QEMU 11.1.0"},
        "static": {"total_instructions": 42, "by_mnemonic": {"add": 10},
                   "functions": [{"name": "main", "instructions": 42,
                                  "section": ".text"}],
                   "text_bytes": 168},
        "dynamic": {"total": 1000, "by_class": {"integer": 1000}, "calls": 5},
        "run": {"stdout_sha256": "x", "exit_code": 0, "wall_time_median_s": None},
        "energy": {"score": 1000, "weights_used": {}, "disclaimer": "d"},
    }
    (workdir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")


# ---------------------------------------------------------------------------
# 离线 glue 测试（无工具链、无网络依赖）
# ---------------------------------------------------------------------------

def test_run_optimize_glue(monkeypatch, workdir):
    _seed_task_dir(workdir)
    seen: dict = {}

    monkeypatch.setattr(pipeline, "static_stats",
                        lambda path: pipeline.StaticStats(0, {}, [], 0))
    monkeypatch.setattr(pipeline, "parse_trace",
                        lambda path, max_bytes=None: object())
    monkeypatch.setattr(pipeline.rules, "analyze",
                        lambda src, disasm, metrics, trace=None: [dict(FINDING)])

    def fake_suggest(**kwargs):
        seen["suggest_kwargs"] = kwargs
        return SuggestOutcome(suggestions=[dict(SUGGESTION), dict(SUGGESTION)])

    monkeypatch.setattr(pipeline.llm_agent, "suggest", fake_suggest)

    def fake_verify_many(candidates, baseline, wd, **kwargs):
        seen["candidates"] = [(sid, Path(p).read_text(encoding="utf-8"))
                              for sid, p in candidates]
        seen["baseline"] = baseline
        return (dict(VERDICT_ACCEPTED), [dict(VERDICT_ACCEPTED)])

    monkeypatch.setattr(pipeline.verifier, "verify_many", fake_verify_many)

    ui_cfg = LlmConfig(base_url="https://api.deepseek.com",
                       api_key="k-ui-test", model="deepseek-flash")
    res = pipeline.run_optimize(workdir, llm_cfg=ui_cfg)

    assert res.ok and res.state == "DONE"
    assert res.llm_mode == "llm" and res.llm_degraded_reason == ""
    assert len(res.findings) == 1 and len(res.suggestions) == 2

    # 界面配置原样透传给 suggest（Key 不落盘、不进 optimize.json）
    assert seen["suggest_kwargs"]["cfg"] is ui_cfg
    assert "api_key" not in (workdir / "optimize.json").read_text(encoding="utf-8")

    # LLM 上下文按约定组装：源码路径 + 实测 metrics + 规则发现
    assert seen["suggest_kwargs"]["src"] == workdir / "src" / "app.c"
    assert seen["suggest_kwargs"]["metrics"]["dynamic"]["total"] == 1000
    assert seen["suggest_kwargs"]["findings"][0]["rule_id"] == "R1"

    # 候选落盘 + suggestion_id 命名（docs/11 D1：llm-01/llm-02）
    assert [sid for sid, _ in seen["candidates"]] == ["llm-01", "llm-02"]
    assert seen["candidates"][0][1] == SUGGESTION["modified_source"]
    assert (workdir / "suggestions" / "llm-01.c").is_file()

    # baseline 组装：golden 来自 stdout.txt，其余来自 metrics.json 落盘证据
    bl = seen["baseline"]
    assert bl["stdout"] == "sum=5050\n"
    assert bl["arch"] == "rv64gc" and bl["opt"] == "O0"
    assert bl["task_id"] == "t-test"
    assert bl["static_stub"]["total_instructions"] == 42
    assert bl["meta"]["toolchain"] == "xPack GCC 15.2.0"

    # 结果透传 + 证据 JSON 落盘 + summary 可序列化（UI 会话依赖）
    assert res.verdicts[0]["state"] == "ACCEPTED"
    assert res.best_verdict is not None and res.best_verdict["promoted"]
    opt_json = json.loads((workdir / "optimize.json").read_text(encoding="utf-8"))
    assert opt_json["llm_mode"] == "llm"
    assert opt_json["suggestions"][0]["suggestion_id"] == "llm-01"
    json.dumps(res.summary(), ensure_ascii=False)


def test_run_optimize_degraded_no_candidates(monkeypatch, workdir):
    """LLM 降级（未配置）时：仅规则取证、不触发验证、状态仍 DONE（NFR5）。"""
    _seed_task_dir(workdir)
    monkeypatch.setattr(pipeline, "static_stats",
                        lambda path: pipeline.StaticStats(0, {}, [], 0))
    monkeypatch.setattr(pipeline, "parse_trace",
                        lambda path, max_bytes=None: object())
    monkeypatch.setattr(pipeline.rules, "analyze",
                        lambda src, disasm, metrics, trace=None: [dict(FINDING)])

    def fake_suggest(**kwargs):
        return SuggestOutcome(
            degraded_reason="未填写 DeepSeek API Key（界面「优化建议」面板），当前为规则引擎模式")

    monkeypatch.setattr(pipeline.llm_agent, "suggest", fake_suggest)

    def fail_verify(*args, **kwargs):
        raise AssertionError("无候选时不应触发验证闭环")

    monkeypatch.setattr(pipeline.verifier, "verify_many", fail_verify)

    res = pipeline.run_optimize(workdir)

    assert res.ok and res.state == "DONE"
    assert res.llm_mode == "rules"
    assert res.suggestions == [] and res.verdicts == [] and res.best_verdict is None
    assert "未填写" in res.llm_degraded_reason and "规则引擎模式" in res.llm_degraded_reason
    assert len(res.findings) == 1
    assert not (workdir / "suggestions").exists() or not list((workdir / "suggestions").glob("*.c"))
    assert (workdir / "optimize.json").is_file()


def test_run_optimize_missing_metrics(workdir):
    """任务目录不完整（未体检）时：ERROR 且给出可操作提示，不抛异常。"""
    res = pipeline.run_optimize(workdir)
    assert not res.ok and res.state == "ERROR"
    assert "metrics.json" in (res.error or "")


def test_run_optimize_missing_stdout(workdir):
    """有 metrics 但缺 stdout.txt（golden 依据）时：ERROR 提示重新体检。"""
    _seed_task_dir(workdir)
    (workdir / "stdout.txt").unlink()
    res = pipeline.run_optimize(workdir)
    assert not res.ok and res.state == "ERROR"
    assert "stdout.txt" in (res.error or "")


def test_run_optimize_rejects_truncated_baseline(workdir):
    _seed_task_dir(workdir)
    metrics_path = workdir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["meta"]["diagnostics"] = {"stdout_truncated": True}
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    res = pipeline.run_optimize(workdir)
    assert res.state == "ERROR"
    assert "截断" in (res.error or "")


def test_run_optimize_rejects_truncated_evidence_trace(workdir, monkeypatch):
    _seed_task_dir(workdir)
    monkeypatch.setattr(pipeline, "parse_trace",
                        lambda path, max_bytes=None: SimpleNamespace(truncated=True))
    res = pipeline.run_optimize(workdir)
    assert res.state == "ERROR"
    assert "trace" in (res.error or "") and "截断" in res.error


# ---------------------------------------------------------------------------
# 集成测试：真实 GCC + QEMU（工具链缺失自动 skip）
# ---------------------------------------------------------------------------

CANDIDATE_C = """\
#include <stdio.h>

int main(void) {
    printf("sum=5050\\n");
    return 0;
}
"""

VERDICT_ACCEPTED = {
    "task_id": "t-test", "suggestion_id": "llm-01", "state": "ACCEPTED",
    "trace": ["PROPOSED", "COMPILED", "TESTED", "MEASURED", "ACCEPTED"],
    "checks": {"compiled": True, "stdout_identical": True, "instr_reduced": True},
    "before": {"dynamic.total": 1000, "energy.score": 1000},
    "after": {"dynamic.total": 900, "energy.score": 900},
    "rollback_done": False, "promoted": True, "failure_reason": None,
    "candidate_dir": "nowhere",
    "additive": {"improvement": {"instr_delta": -100, "energy_delta": -100}},
}


@requires_gcc
@requires_qemu
def test_run_optimize_end_to_end(monkeypatch, workdir):
    """体检 → 桥接层（LLM 注入离线候选）→ 真实验证闭环 ACCEPTED。"""
    src = workdir / "hello.c"
    src.write_text(HELLO_C, encoding="utf-8")
    r = pipeline.run_checkup(src, arch="rv64gc", opt="O0", benchmark="hello",
                             workroot=workdir, verify_determinism=False)
    assert r.ok, f"体检失败: {r.error}"

    def fake_suggest(**kwargs):
        return SuggestOutcome(suggestions=[{
            "title": "公式化改写",
            "explanation": "1..100 等差求和直接计算，输出不变（正确性由闭环验证）",
            "modified_source": CANDIDATE_C,
            "expected_gain": "high", "risk": "low", "confidence": 0.9,
        }])

    monkeypatch.setattr(pipeline.llm_agent, "suggest", fake_suggest)

    res = pipeline.run_optimize(r.workdir)
    assert res.ok, res.error
    assert res.verdicts, "应产生一个候选 verdict"
    v = res.verdicts[0]
    assert v["state"] == "ACCEPTED", v.get("failure_reason")
    assert v["before"]["dynamic.total"] > v["after"]["dynamic.total"]
    assert res.best_verdict is not None and res.best_verdict["promoted"]
    assert (r.workdir / "suggestions" / "llm-01.c").is_file()
    assert (r.workdir / "candidates" / "llm-01" / "verdict.json").is_file()
    assert res.reports and Path(res.reports[0]).is_file()
