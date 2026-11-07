# -*- coding: utf-8 -*-
"""M4 DoD 证据生成器：10 个基准全量扫描。

每基准：编译（O0，b06 用 rv64i 演示软浮点，其余 rv64gc）→ QEMU 运行 →
trace 解析 → 指标聚合 → 规则分析，产物落盘 workdir/m4_scan/<bench>/。
验收：每基准至少触发 1 条规则且证据字段齐全（docs/06 §3 预期规则对照）。

用法：.venv/Scripts/python.exe scripts/m4_scan.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from compiler.disassemble import disassemble, static_stats  # noqa: E402
from compiler.riscv_compile import compile_source  # noqa: E402
from optimizer.rules import analyze  # noqa: E402
from profiler.metrics import aggregate  # noqa: E402
from profiler.runner import run_baremetal  # noqa: E402
from profiler.trace_parser import parse_trace  # noqa: E402

#: 06 文档 §3 预期触发的规则（用于对照汇报，不作为判定条件）
EXPECTED = {
    "b01_sum_array": ["R6", "R7"], "b02_matrix_mul": ["R1", "R2", "R7"],
    "b03_crc32": ["R6"], "b04_string_search": ["R1", "R3"],
    "b05_bubble_sort": ["R1", "R6"], "b06_grayscale": ["R9"],
    "b07_fib": ["R3"], "b08_dot_product": ["R2", "R6"],
    "b09_strlen_loop": ["R1"], "b10_window_sum": ["R1", "R7"],
}
ARCH = {name: ("rv64i" if name == "b06_grayscale" else "rv64gc") for name in EXPECTED}

OUT = REPO / "workdir" / "m4_scan"
EVIDENCE_KEYS = ("asm_snippet", "dynamic_calls", "dynamic_loads", "dynamic_branch",
                 "sext_w_count", "interior_sp_ops", "ratio")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    t0 = time.perf_counter()

    for name, arch in ARCH.items():
        bench_dir = OUT / name
        src = REPO / "benchmarks" / f"{name}.c"
        result = compile_source(src, arch, "O0", bench_dir)
        if not result.ok:
            rows.append({"benchmark": name, "error": f"compile: {result.stderr_tail[:150]}"})
            print("FAIL", name, "compile"); continue
        run = run_baremetal(result.elf, bench_dir)
        if not run.ok:
            rows.append({"benchmark": name, "error": f"run: rc={run.exit_code} {run.stderr_tail[:150]}"})
            print("FAIL", name, "run"); continue
        stats = static_stats(disassemble(result.elf, bench_dir))
        trace = parse_trace(run.trace_path)
        metrics = aggregate(stats, trace, {
            "benchmark": name, "arch": arch, "opt": "O0",
            "stdout": run.stdout, "exit_code": run.exit_code,
        })
        findings = analyze(src, stats, metrics, trace=trace)
        (bench_dir / "findings.json").write_text(
            json.dumps({"stdout": run.stdout, "dynamic_total": metrics["dynamic"]["total"],
                        "findings": findings}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        rules = sorted({f["rule_id"] for f in findings})
        user_rules = sorted({f["rule_id"] for f in findings if f["location"].get("scope") == "user"})
        rows.append({
            "benchmark": name, "arch": arch,
            "dynamic_total": metrics["dynamic"]["total"],
            "rules_fired": rules, "user_scope_rules": user_rules,
            "expected": EXPECTED[name],
            "findings": len(findings),
            "evidence_ok": all(bool(f["evidence"]) for f in findings),
        })
        print("OK", name, "rules:", rules, "user:", user_rules, flush=True)

    summary = {
        "toolchain": "xpack riscv-none-elf-gcc 15.2.0 / qemu 11.1.0",
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "results": rows,
        "dod_pass": all("error" not in r and r["rules_fired"] and r["evidence_ok"] for r in rows),
    }
    (OUT / "scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDoD(每基准≥1条规则且证据齐全): {'PASS' if summary['dod_pass'] else 'FAIL'}"
          f" in {summary['elapsed_s']}s")
    return 0 if summary["dod_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
