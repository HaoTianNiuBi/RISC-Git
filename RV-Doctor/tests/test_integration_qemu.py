"""profiler 集成测试（真实 QEMU；tools/qemu 缺失时自动 skip）。

对应 docs/06 §5 M2 验收：
* 冒烟程序真实跑通（ok、stdout、退出码）
* 确定性校验：同一镜像连续两次运行，动态指令总数与类别分布完全相等
* 超时可靠终止（自跳转镜像 + 小超时）
* 32 位路径（qemu-system-riscv32）
"""
from __future__ import annotations

import pytest

from profiler.metrics import aggregate
from profiler.runner import RunnerError, run_baremetal
from profiler.trace_parser import parse_trace
from tests.conftest import requires_qemu

# smoke 程序的手工 oracle（见 tests/test_trace_parser.py 顶部说明）
# 程序区（DRAM）动态执行恒为 33 条，是稳定的确定性 oracle；trace 全量计数
# 还包含 QEMU 引导 ROM 的 6 条 stub，其首条会偶发重复执行（全量 39/40 抖动，
# 见 profiler/metrics.DRAM_BASE 注释），故真实运行的测试锚定程序区计数。
PROGRAM_EXEC = 33
DRAM_BASE = 0x80000000
PC_LOOP_ADDI = 0x80000020
PC_LOOP_BLT = 0x80000024


def _program_exec(stats) -> int:
    """程序区（DRAM 基址以上）动态指令数——确定性口径。"""
    return sum(c for pc, c in stats.exec_counts.items() if pc >= DRAM_BASE)


def _parse_meta(result, benchmark: str, arch: str) -> dict:
    return {
        "task_id": "t-itest",
        "benchmark": benchmark,
        "arch": arch,
        "opt": "O0",
        "toolchain": "hand-assembled (scripts/gen_smoke_elf.py)",
        "stdout": result.stdout,
        "exit_code": result.exit_code,
    }


@requires_qemu
def test_run_smoke64_real_qemu(workdir, smoke64):
    result = run_baremetal(smoke64, workdir, bitness=64)
    assert result.ok, f"run failed: rc={result.exit_code} stderr={result.stderr_tail!r}"
    assert "rv-doctor smoke: loop done" in result.stdout
    assert result.exit_code == 0
    assert result.trace_path is not None and result.trace_path.exists()
    # 产物证据链落盘
    assert (workdir / "stdout.txt").read_text(encoding="utf-8").strip()
    assert (workdir / "trace.log").exists()

    stats = parse_trace(result.trace_path)
    assert _program_exec(stats) == PROGRAM_EXEC
    assert stats.exec_counts[PC_LOOP_ADDI] == 10
    assert stats.exec_counts[PC_LOOP_BLT] == 10


@requires_qemu
def test_run_smoke64_no_trace_capture(workdir, smoke64):
    result = run_baremetal(smoke64, workdir, capture_trace=False, bitness=64)
    assert result.ok
    assert result.trace_path is None
    assert not (workdir / "trace.log").exists()


@requires_qemu
def test_determinism_two_runs_identical(workdir, smoke64):
    """确定性校验（docs/06 §5.1）：两次运行动态指令数与类别分布完全相等。"""
    reports = []
    for _ in range(2):
        result = run_baremetal(smoke64, workdir, timeout=60.0, bitness=64)
        assert result.ok
        stats = parse_trace(result.trace_path)
        reports.append(
            aggregate({"total_instructions": 17, "by_mnemonic": {}, "functions": [],
                       "text_bytes": 68},
                      stats, _parse_meta(result, "smoke64", "rv64i"))
        )
    d1, d2 = reports[0]["dynamic"], reports[1]["dynamic"]
    assert d1["total"] == d2["total"] == PROGRAM_EXEC
    assert d1["by_class"] == d2["by_class"]
    assert reports[0]["run"]["stdout_sha256"] == reports[1]["run"]["stdout_sha256"]


@requires_qemu
def test_timeout_kills_runnable(workdir, hang64):
    result = run_baremetal(hang64, workdir, timeout=4.0, bitness=64)
    assert result.timed_out
    assert not result.ok
    assert result.trace_path is not None  # trace 日志仍在（证据保留）


@requires_qemu
def test_run_smoke32_real_qemu(workdir, smoke32):
    result = run_baremetal(smoke32, workdir, bitness=32)
    assert result.ok, f"run failed: rc={result.exit_code} stderr={result.stderr_tail!r}"
    assert "rv-doctor smoke: loop done" in result.stdout
    stats = parse_trace(result.trace_path)
    assert _program_exec(stats) == PROGRAM_EXEC
    assert stats.exec_counts[PC_LOOP_ADDI] == 10
    assert stats.exec_counts[PC_LOOP_BLT] == 10


def test_missing_image_raises(workdir):
    with pytest.raises(RunnerError):
        run_baremetal(workdir / "nope.bin", workdir)
