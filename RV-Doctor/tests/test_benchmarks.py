"""基准回归层（docs/06 §7 回归行：10 基准 × 2 项 = golden 比对 + 指标快照零浮动）。

依赖本机工具链（gcc + qemu），且整套约 1~2 分钟 → 标记 slow，
一键脚本 scripts\\run_all_checks.bat 与 `-m "slow"` 时执行，日常开发可跳过。

指标是确定性的（docs/06 §5.4）：与快照的允许浮动为零；不一致即回归
（通常是工具链/crt0 变更 —— 此时用 scripts\\freeze_benchmarks.py 重新冻结，
并在提交说明中注明原因）。
"""
from __future__ import annotations

import json

import pytest

from pipeline import run_checkup, stdout_matches
from tests.toolchain import requires_gcc, requires_qemu

BENCH_DIR = pytest.importorskip("pathlib").Path(__file__).resolve().parent.parent / "benchmarks"
EXPECTED = BENCH_DIR / "expected"
SNAPSHOTS = BENCH_DIR / "snapshots"

NAMES = sorted(p.stem for p in BENCH_DIR.glob("b*.c"))
if not NAMES:
    pytest.skip("benchmarks/ 下没有基准源码", allow_module_level=True)


def _require_frozen(name: str):
    golden = EXPECTED / f"{name}.out"
    snap = SNAPSHOTS / f"{name}.json"
    if not (golden.is_file() and snap.is_file()):
        pytest.skip(f"{name} 未冻结（先跑 scripts/freeze_benchmarks.py）")
    return golden, json.loads(snap.read_text(encoding="utf-8"))


def _dig(d: dict, dotted: str):
    cur = d
    for k in dotted.split("."):
        cur = cur[k]
    return cur


@requires_gcc
@requires_qemu
@pytest.mark.slow
@pytest.mark.parametrize("name", NAMES)
def test_benchmark_golden_and_snapshot(name, workdir):
    golden_path, snapshot = _require_frozen(name)

    r = run_checkup(
        BENCH_DIR / f"{name}.c",
        arch=snapshot["meta"]["arch"],
        opt=snapshot["meta"]["opt"],
        benchmark=name,
        workroot=workdir,
    )
    assert r.state == "DONE", f"{name}: state={r.state} err={r.error!r}"

    # 1) golden stdout 比对（docs/06 §4 规则）
    ok, diff = stdout_matches(r.stdout, golden_path.read_text(encoding="utf-8"))
    assert ok, f"{name} stdout 漂移: {diff}"

    # 2) 指标快照零浮动（确定性指标）
    fresh = r.metrics
    assert fresh["dynamic"]["total"] == _dig(snapshot, "dynamic.total"), (
        f"{name} 动态指令数漂移: {fresh['dynamic']['total']} != "
        f"{snapshot['dynamic']['total']}（工具链/crt0 变更？重新冻结）"
    )
    assert fresh["dynamic"]["by_class"] == _dig(snapshot, "dynamic.by_class"), (
        f"{name} 类别分布漂移"
    )
    assert fresh["static"]["total_instructions"] == _dig(
        snapshot, "static.total_instructions"
    ), f"{name} 静态指令数漂移"
