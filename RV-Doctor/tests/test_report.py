"""reports/report.py 单元测试：合成 before/after metrics 与 verdict。

锁定 P8 铁律：表格数字只能来自 JSON（注入哨兵值验证回显）；缺失渲染 "—"；
变化率只由 JSON 数值推导；能耗免责声明与 TCG 边界声明必须在场。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from reports.report import (
    ENERGY_DISCLAIMER,
    TCG_BOUNDARY,
    render_comparison_report,
)

REPO = Path(__file__).resolve().parents[1]


def metrics(total=100_000, memory=30_000, branch=20_000, calls=10_000,
            static_total=5_000, text_bytes=20_000, energy=250_000,
            arch="rv64gc", opt="O0", benchmark="b09_strlen_loop",
            sha="a" * 64, **meta_extra):
    by = {"integer": total - memory - branch, "memory": memory, "branch": branch,
          "jump": 0, "muldiv": 0, "float": 0, "system": 0}
    return {
        "meta": {"task_id": "t-20260917-001", "benchmark": benchmark,
                 "arch": arch, "opt": opt,
                 "toolchain": "xpack-gcc 15.2.0", "qemu": "11.1.0", **meta_extra},
        "static": {"total_instructions": static_total, "text_bytes": text_bytes,
                   "by_mnemonic": {}, "functions": []},
        "dynamic": {"total": total, "by_class": by, "calls": calls},
        "run": {"stdout_sha256": sha, "exit_code": 0, "wall_time_median_s": None},
        "energy": {"score": energy, "weights_used": {}, "disclaimer": "相对能耗估算"},
    }


def verdict(state="ACCEPTED", before_total=100_000, after_total=80_000, **extra):
    v = {
        "task_id": "t-20260917-001", "suggestion_id": "R1-001",
        "state": state,
        "trace": ["PROPOSED", "COMPILED", "TESTED", "MEASURED", state],
        "checks": {"compiled": True, "stdout_identical": True, "instr_reduced": True},
        "before": {"dynamic.total": before_total, "energy.score": 250_000},
        "after": {"dynamic.total": after_total, "energy.score": 210_000},
        "rollback_done": False, "failure_reason": None,
        "additive": {"improvement": {"instr_delta": after_total - before_total,
                                     "energy_delta": -40_000}},
    }
    v.update(extra)
    return v


# ---------- ACCEPTED 路径 ----------


def test_accepted_report_numbers_come_from_json():
    md = render_comparison_report(metrics(), metrics(total=80_000, memory=25_000, energy=210_000),
                                  verdict())
    assert "100,000" in md and "80,000" in md       # 动态总数回显（千分位）
    assert "30,000" in md and "25,000" in md        # 访存指令
    assert "10,000" in md                            # 函数调用
    assert "5,000" in md and "20,000" in md          # 静态指令/代码段字节
    assert "250,000" in md and "210,000" in md       # 能耗分
    assert "-20.0%" in md                            # 动态总数变化率
    assert "+25.0%" not in md                        # 未提供的数值不得反向出现


def test_accepted_report_verdict_and_conclusion():
    v = verdict()
    md = render_comparison_report(metrics(), metrics(total=80_000), v)
    assert "ACCEPTED" in md
    assert "PROPOSED → COMPILED → TESTED → MEASURED → ACCEPTED" in md
    assert "compiled=PASS" in md and "stdout_identical=PASS" in md
    assert "已被采纳" in md and "20,000" in md       # 结论引用 instr_delta


def test_accepted_report_does_not_claim_instruction_reduction_for_energy_only_gain():
    v = verdict(before_total=100_000, after_total=110_000)
    v["additive"]["improvement"] = {"instr_delta": 10_000, "energy_delta": -40_000}
    md = render_comparison_report(metrics(), metrics(total=110_000, energy=210_000), v)
    assert "能耗估算分减少 40,000" in md
    assert "动态指令减少" not in md


def test_change_rate_math():
    md = render_comparison_report(metrics(memory=10_000), metrics(memory=5_000, total=90_000),
                                  verdict())
    assert "-50.0%" in md  # 访存 10000 → 5000


# ---------- REJECTED 路径 ----------


def test_rejected_report_shows_reason_and_rollback():
    v = verdict(state="REJECTED", before_total=100_000, after_total=120_000,
                checks={"compiled": True, "stdout_identical": True, "instr_reduced": False},
                trace=["PROPOSED", "COMPILED", "TESTED", "MEASURED", "REJECTED"],
                failure_reason="指标未改善：动态指令 100000 → 120000", rollback_done=True)
    md = render_comparison_report(metrics(), metrics(total=120_000), v)
    assert "REJECTED" in md and "已回滚" in md
    assert "指标未改善" in md
    assert "已被采纳" not in md


# ---------- verdict=None（直接对比，跨架构场景） ----------


def test_direct_compare_uses_sha256_verdict_free():
    same = render_comparison_report(metrics(), metrics(sha="a" * 64), None)
    assert "PASS（stdout SHA256 相同）" in same and "未走验证闭环" in same
    diff = render_comparison_report(metrics(), metrics(sha="b" * 64), None)
    assert "FAIL（stdout SHA256 不同）" in diff


def test_direct_compare_arch_change_annotated():
    md = render_comparison_report(metrics(arch="rv64i"), metrics(arch="rv64gc"), None)
    assert "优化前 `rv64i / -O0` → 优化后 `rv64gc / -O0`" in md


# ---------- 缺失数据不编造 ----------


def test_missing_values_render_as_dash():
    empty = {"meta": {}, "static": {}, "dynamic": {}, "run": {}, "energy": {}}
    md = render_comparison_report(empty, empty, None)
    assert "—" in md
    assert "nan" not in md.lower() and "inf" not in md.lower()


def test_zero_before_renders_dash_not_inf():
    m = metrics()
    m["dynamic"]["by_class"]["float"] = 0
    m2 = metrics()
    m2["dynamic"]["by_class"]["float"] = 0
    md = render_comparison_report(m, m2, None)
    float_row = next(line for line in md.splitlines() if line.startswith("| 浮点"))
    assert float_row.endswith("| — |")  # 0→0 变化率不定义，渲染 —


def test_disclaimers_always_present():
    md = render_comparison_report(metrics(), metrics(), verdict())
    assert ENERGY_DISCLAIMER in md and TCG_BOUNDARY in md


def test_sources_footnote_rendered():
    md = render_comparison_report(metrics(), metrics(), verdict(),
                                  sources={"优化前 metrics": "workdir/t/before.json",
                                           "verdict": "workdir/t/verdict.json"})
    assert "`workdir/t/before.json`" in md and "`workdir/t/verdict.json`" in md


# ---------- CLI 端到端 ----------


def test_cli_generates_report_file(tmp_path: Path):
    b, a, v = metrics(), metrics(total=80_000), verdict()
    pb, pa, pv = tmp_path / "b.json", tmp_path / "a.json", tmp_path / "v.json"
    pb.write_text(json.dumps(b), encoding="utf-8")
    pa.write_text(json.dumps(a), encoding="utf-8")
    pv.write_text(json.dumps(v), encoding="utf-8")
    out = tmp_path / "report.md"
    r = subprocess.run(
        [sys.executable, "-m", "reports.report", str(pb), str(pa), str(pv), "-o", str(out)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    md = out.read_text(encoding="utf-8")
    assert "80,000" in md and "ACCEPTED" in md
