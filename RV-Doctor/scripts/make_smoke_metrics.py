#!/usr/bin/env python3
"""M2 验收产物：跑冒烟程序 -> 解析 trace -> 聚合 -> workdir/smoke64/metrics.json。

产出为真实工具链数据（QEMU 11.1.0 实测），非手工构造；同时打印手工核对表
（循环 addi/blt 各 10 次、程序区总数 33）供 docs/06 §1 的"手工可复核"流程。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from profiler.metrics import aggregate  # noqa: E402
from profiler.runner import run_baremetal, qemu_version  # noqa: E402
from profiler.trace_parser import parse_trace  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
WORK = REPO / "workdir" / "smoke64"

# smoke 程序静态统计（手工编码，17 条指令，来源 scripts/gen_smoke_elf.py）
STATIC = {
    "total_instructions": 17,
    "text_bytes": 68,
    "by_mnemonic": {"auipc": 2, "addi": 6, "mv": 1, "slli": 2,
                    "ebreak": 2, "srai": 2, "blt": 1, "jal": 1},
    "functions": [{"name": "_start", "size_instructions": 17, "section": ".text"}],
}


def main() -> None:
    image = REPO / "tests" / "fixtures" / "smoke64.bin"
    result = run_baremetal(image, WORK, bitness=64)
    assert result.ok, f"run failed: rc={result.exit_code} {result.stderr_tail!r}"

    trace = parse_trace(result.trace_path)
    qemu_ver = qemu_version((REPO / "tools" / "qemu" / "qemu-system-riscv64.exe"))
    report = aggregate(
        STATIC,
        trace,
        run_meta={
            "task_id": "t-20260917-m2-smoke",
            "benchmark": "smoke64 (hand-assembled)",
            "arch": "rv64i",
            "opt": "O0",
            "toolchain": "hand-encoded (scripts/gen_smoke_elf.py); no GCC involved",
            "qemu": qemu_ver,
            "stdout": result.stdout,
            "exit_code": result.exit_code,
            "wall_times": [result.wall_time_s],
        },
    )
    out = WORK / "metrics.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 手工核对表 ----
    pc_addi, pc_blt = 0x80000020, 0x80000024
    rom = sum(c for pc, c in trace.exec_counts.items() if pc < 0x80000000)
    prog = trace.total_exec - rom
    print(f"metrics.json -> {out}")
    print(f"run.ok={result.ok} stdout={result.stdout!r}")
    print(f"trace 全量={trace.total_exec}（引导 ROM {rom} + 程序区 {prog}）")
    print(f"手工核对: addi@0x80000020 = {trace.exec_counts[pc_addi]} (期望 10)")
    print(f"手工核对: blt@0x80000024  = {trace.exec_counts[pc_blt]} (期望 10)")
    print(f"dynamic.total = {report['dynamic']['total']} (期望 33)")
    print(f"by_class = {report['dynamic']['by_class']}")
    print(f"calls = {report['dynamic']['calls']} (期望 0)")
    print(f"energy.score = {report['energy']['score']}")


if __name__ == "__main__":
    main()
