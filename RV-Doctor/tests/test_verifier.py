"""verifier 集成测试（M5：真实 gcc+QEMU，工具链缺失时自动 skip）。

对应 docs/05 M5 DoD 与 docs/06 §6 状态机四条路径：
1. 接受路径：公式化改写 hello → 动态指令骤降 → ACCEPTED；
2. 编译失败路径：语法错误 → REJECTED + apply_to 回滚；
3. 输出不一致路径：改打印值 → REJECTED（首个差异行可读）；
4. 指标未改善路径：候选与原版相同 → REJECTED。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from compiler.disassemble import disassemble, static_stats
from compiler.riscv_compile import compile_source
from optimizer.verifier import _slug, stdout_diff, verify, verify_many
from profiler.metrics import aggregate
from profiler.runner import run_baremetal
from profiler.trace_parser import parse_trace
from tests.conftest import requires_qemu
from tests.toolchain import requires_gcc

OPT_FORMULA = """#include <stdio.h>

int main(void) {
    /* 循环求和改为等差数列公式（行为不变：sum=5050） */
    int sum = 100 * 101 / 2;
    printf("sum=%d\\n", sum);
    return 0;
}
"""

OPT_BROKEN = """#include <stdio.h>

int main(void) {
    int sum = ;   /* 语法错误 */
    printf("sum=%d\\n", sum);
    return 0;
}
"""

OPT_WRONG_OUTPUT = """#include <stdio.h>

int main(void) {
    int sum = 0;
    for (int i = 1; i <= 100; i++) {
        sum += i;
    }
    printf("sum=%d\\n", sum + 1);   /* 行为改变 */
    return 0;
}
"""


def _make_baseline(src, workdir, arch="rv64i", opt="O0") -> dict:
    """编译+运行+统计，构造 verify() 的 baseline（与 pipeline 将来的构造一致）。"""
    bdir = workdir / "baseline"
    cres = compile_source(src, arch, opt, bdir)
    assert cres.ok, cres.stderr_tail
    rres = run_baremetal(cres.elf, bdir)
    assert rres.ok, rres.stderr_tail
    disasm = disassemble(cres.elf, bdir)
    static = static_stats(disasm)
    stats = parse_trace(rres.trace_path)
    metrics = aggregate(
        static, stats,
        {"task_id": "t-m5", "arch": arch, "opt": opt, "toolchain": "test", "qemu": "test",
         "stdout": rres.stdout, "exit_code": rres.exit_code},
    )
    assert metrics["dynamic"]["total"] > 0
    return {
        "stdout": rres.stdout,
        "metrics": metrics,
        "arch": arch,
        "opt": opt,
        "task_id": "t-m5",
        "suggestion_id": "S-TEST",
        "static_stub": {
            "total_instructions": static.total_instructions,
            "by_mnemonic": static.by_mnemonic,
            "functions": static.functions,
            "text_bytes": static.text_bytes,
        },
        "meta": {"arch": arch, "opt": opt, "toolchain": "test", "qemu": "test"},
    }


# ---------------------------------------------------------------------------
# stdout_diff 纯单元（无工具链依赖）
# ---------------------------------------------------------------------------

def test_stdout_diff_identical_and_newline_tolerance():
    assert stdout_diff("sum=5050\n", "sum=5050\n")["identical"]
    assert stdout_diff("sum=5050\r\n", "sum=5050\n")["identical"]      # CRLF 归一
    assert stdout_diff("sum=5050", "sum=5050\n")["identical"]          # 末尾至多一个换行
    d = stdout_diff("sum=5050\n", "sum=5051\n")
    assert not d["identical"] and d["first_diff_line"] == 1
    assert "5051" in d["excerpt"] and "5050" in d["excerpt"]


# ---------------------------------------------------------------------------
# 状态机四条路径（真实工具链）
# ---------------------------------------------------------------------------

@requires_gcc
@requires_qemu
def test_accept_path_formula_rewrite(hello_src, workdir):
    baseline = _make_baseline(hello_src, workdir)
    cand = workdir / "opt_formula.c"
    cand.write_text(OPT_FORMULA, encoding="utf-8")

    verdict = verify(cand, baseline, workdir)

    assert verdict["state"] == "ACCEPTED", verdict["failure_reason"]
    assert verdict["trace"] == ["PROPOSED", "COMPILED", "TESTED", "MEASURED", "ACCEPTED"]
    assert all(verdict["checks"].values())
    assert verdict["after"]["dynamic.total"] < verdict["before"]["dynamic.total"]
    assert verdict["additive"]["improvement"]["instr_delta"] < 0
    assert not verdict["rollback_done"]
    # verdict.json 落盘且内容一致（证据链）
    saved = json.loads((workdir / "candidates" / "S-TEST" / "verdict.json").read_text(encoding="utf-8"))
    assert saved["state"] == "ACCEPTED"
    # 候选版完整指标落盘（reports/report.py after 侧的数据来源）
    after_m = json.loads(
        (workdir / "candidates" / "S-TEST" / "metrics.json").read_text(encoding="utf-8")
    )
    assert after_m["dynamic"]["total"] == verdict["after"]["dynamic.total"]
    # after 的静态统计必须来自候选 ELF 的真实反汇编（而非 baseline 占位）
    assert after_m["static"]["total_instructions"] > 0
    assert after_m["static"]["total_instructions"] != baseline["metrics"]["static"]["total_instructions"]


@requires_gcc
@requires_qemu
def test_reject_compile_error_with_rollback(hello_src, workdir):
    baseline = _make_baseline(hello_src, workdir)
    live = workdir / "live.c"          # 模拟 pipeline 已把候选应用到工作副本
    live.write_text("/* 原版占位 */", encoding="utf-8")
    original_bytes = live.read_bytes()
    cand = workdir / "opt_broken.c"
    cand.write_text(OPT_BROKEN, encoding="utf-8")

    verdict = verify(cand, baseline, workdir, apply_to=live)

    assert verdict["state"] == "REJECTED"
    assert verdict["checks"]["compiled"] is False
    assert "编译失败" in verdict["failure_reason"]
    assert verdict["rollback_done"] is True            # 自动回滚
    assert live.read_bytes() == original_bytes         # 原文件内容恢复


@requires_gcc
@requires_qemu
def test_reject_output_mismatch(hello_src, workdir):
    baseline = _make_baseline(hello_src, workdir)
    cand = workdir / "opt_wrong.c"
    cand.write_text(OPT_WRONG_OUTPUT, encoding="utf-8")

    verdict = verify(cand, baseline, workdir)

    assert verdict["state"] == "REJECTED"
    assert verdict["checks"]["compiled"] is True
    assert verdict["checks"]["stdout_identical"] is False
    assert "输出不一致" in verdict["failure_reason"]
    assert verdict["additive"]["stdout_diff"]["first_diff_line"] == 1


@requires_gcc
@requires_qemu
def test_reject_no_improvement(hello_src, workdir):
    baseline = _make_baseline(hello_src, workdir)
    cand = hello_src  # 与原版完全相同 → 指标不可能改善

    verdict = verify(cand, baseline, workdir)

    assert verdict["state"] == "REJECTED"
    assert verdict["checks"]["compiled"] and verdict["checks"]["stdout_identical"]
    assert verdict["checks"]["instr_reduced"] is False
    assert "指标未改善" in verdict["failure_reason"]


@requires_gcc
@requires_qemu
def test_verify_many_promotes_best(hello_src, workdir):
    baseline = _make_baseline(hello_src, workdir)
    # 次优：仅 2 路展开（比原版少但比公式多）；最优：公式
    half_loop = OPT_FORMULA.replace(
        "int sum = 100 * 101 / 2;",
        "int sum = 0;\n    for (int i = 1; i <= 100; i += 2) { sum += i + (i + 1 <= 100 ? i + 1 : 0); }",
    )
    cands = [
        ("S-half", _write(workdir, "c_half.c", half_loop)),
        ("S-formula", _write(workdir, "c_formula.c", OPT_FORMULA)),
    ]
    best, all_v = verify_many(cands, baseline, workdir)

    assert best is not None and best["suggestion_id"] == "S-formula"
    assert best["promoted"] is True
    by_id = {v["suggestion_id"]: v for v in all_v}
    assert by_id["S-half"]["promoted"] is False
    assert by_id["S-half"]["after"]["dynamic.total"] > best["after"]["dynamic.total"]


def _write(workdir, name, text):
    p = workdir / name
    p.write_text(text, encoding="utf-8")
    return p


def test_dot_only_suggestion_id_cannot_escape_candidate_directory(workdir):
    marker = workdir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    candidate = _write(workdir, "candidate.c", "int main(void) { return 0; }")
    verdict = verify(candidate, {"suggestion_id": ".."}, workdir)
    assert verdict["state"] == "REJECTED"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert (Path(verdict["candidate_dir"]) / "verdict.json").is_file()
    assert Path(verdict["candidate_dir"]).parent == workdir / "candidates"


def test_different_suggestion_ids_have_separate_candidate_directories():
    assert _slug("same/id") != _slug("same?id")


def test_verify_rejects_truncated_baseline_before_compiling(workdir):
    candidate = _write(workdir, "candidate.c", "int main(void) { return 0; }")
    baseline = {
        "stdout": "", "arch": "rv64i", "opt": "O0",
        "metrics": {"meta": {"diagnostics": {"trace_truncated": True}}},
    }
    verdict = verify(candidate, baseline, workdir)
    assert verdict["state"] == "REJECTED"
    assert "截断" in verdict["failure_reason"]
    assert not (Path(verdict["candidate_dir"]) / "app.elf").exists()


@requires_gcc
@requires_qemu
def test_verify_rejects_truncated_candidate_trace(hello_src, workdir, monkeypatch):
    import optimizer.verifier as verifier_module

    baseline = _make_baseline(hello_src, workdir)
    candidate = _write(workdir, "formula.c", OPT_FORMULA)
    real_parse = verifier_module.parse_trace
    monkeypatch.setattr(verifier_module, "parse_trace",
                        lambda path: real_parse(path, max_bytes=128))
    verdict = verify(candidate, baseline, workdir)
    assert verdict["state"] == "REJECTED"
    assert "trace" in verdict["failure_reason"] and "截断" in verdict["failure_reason"]


@requires_gcc
@requires_qemu
def test_verify_rejects_trace_without_program_execution(hello_src, workdir, monkeypatch):
    import optimizer.verifier as verifier_module
    from profiler.trace_parser import TraceStats

    baseline = _make_baseline(hello_src, workdir)
    candidate = _write(workdir, "formula.c", OPT_FORMULA)
    monkeypatch.setattr(verifier_module, "parse_trace", lambda path: TraceStats())
    verdict = verify(candidate, baseline, workdir)
    assert verdict["state"] == "REJECTED"
    assert "没有程序区执行记录" in verdict["failure_reason"]
