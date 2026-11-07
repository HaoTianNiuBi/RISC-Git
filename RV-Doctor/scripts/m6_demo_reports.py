# -*- coding: utf-8 -*-
"""M6 演示报告生成器：三个演示基准（07 文档场景 A/B/C）的实测对比报告。

- b02_matrix_mul（场景 A）：rv64gc/O0 源码级优化（i-k-j + 行指针），
  走 optimizer.verifier 完整闭环，预期 ACCEPT；
- b09_strlen_loop（场景 B）：rv64gc/O0 循环不变量外提，完整闭环，预期 ACCEPT；
- b06_grayscale（场景 C）：同一源码跨架构对比（rv64i → rv64gc，R9 建议
  "选择带 F 扩展的架构"），不适用同架构验证闭环，报告以"直接对比"呈现，
  输出一致性用 stdout 比对与 SHA256 双重记录。

所有数字来自真实编译/运行/trace 解析；产物落盘 workdir/m6_reports/<bench>/。
用法：.venv/Scripts/python.exe scripts/m6_demo_reports.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from compiler.disassemble import disassemble, static_stats  # noqa: E402
from compiler.riscv_compile import compile_source  # noqa: E402
from optimizer.rules import analyze  # noqa: E402
from optimizer.verifier import stdout_diff, verify  # noqa: E402
from profiler.metrics import aggregate  # noqa: E402
from profiler.runner import qemu_version, resolve_qemu, run_baremetal  # noqa: E402
from profiler.trace_parser import parse_trace  # noqa: E402
from reports.report import render_comparison_report  # noqa: E402

OUT = REPO / "workdir" / "m6_reports"
_m = re.search(r"version\s+(\S+)", qemu_version(resolve_qemu(64)))
QEMU_VER = _m.group(1) if _m else "unknown"
TOOLCHAIN = "xpack riscv-none-elf-gcc 15.2.0 (binutils 2.45)"


def measure(src: Path, arch: str, opt: str, benchmark: str, task_id: str, wd: Path):
    """编译 → 运行 → 解析 → 聚合，返回 (metrics, stats, trace)。"""
    cres = compile_source(src, arch, opt, wd)
    assert cres.ok, f"{benchmark} {arch}/{opt} 编译失败: {cres.stderr_tail[:200]}"
    rres = run_baremetal(cres.elf, wd)
    assert rres.ok, f"{benchmark} 运行失败: rc={rres.exit_code} {rres.stderr_tail[:200]}"
    stats = static_stats(disassemble(cres.elf, wd))
    trace = parse_trace(rres.trace_path)
    metrics = aggregate(stats, trace, {
        "task_id": task_id, "benchmark": benchmark, "arch": arch, "opt": opt,
        "toolchain": TOOLCHAIN, "qemu": QEMU_VER,
        "stdout": rres.stdout, "exit_code": rres.exit_code,
    })
    return metrics, stats, trace, rres


def demo_closed_loop(name: str, arch: str, opt: str, variant_rel: str,
                     suggestion_id: str, note: str) -> dict:
    """场景 A/B：同架构源码级优化，走完整验证闭环。"""
    task_dir = OUT / name
    task_dir.mkdir(parents=True, exist_ok=True)
    task_id = f"m6-{name}"
    src = REPO / "benchmarks" / f"{name}.c"

    before, stats, trace, run = measure(src, arch, opt, name, task_id, task_dir / "baseline")
    findings = analyze(src, stats, before, trace=trace)
    (task_dir / "findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict = verify(
        REPO / variant_rel,
        {"stdout": run.stdout, "metrics": before, "arch": arch, "opt": opt,
         "task_id": task_id, "suggestion_id": suggestion_id},
        task_dir,
    )
    # after：候选产物目录里重跑一次，拿真实 stdout 与 trace 重建完整 MetricsReport
    cand_dir = task_dir / "candidates" / suggestion_id
    after, _s, _t, _r = measure(cand_dir / "candidate.c", arch, opt, name,
                                task_id, task_dir / "after_run")

    for fname, obj in (("before_metrics.json", before), ("after_metrics.json", after),
                       ("verdict.json", verdict)):
        (task_dir / fname).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    md = render_comparison_report(
        before, after, verdict,
        sources={"优化前 metrics": "before_metrics.json",
                 "优化后 metrics": "after_metrics.json",
                 "verdict": "verdict.json",
                 "规则发现（基线）": "findings.json",
                 "基线 trace": "baseline/trace.log",
                 "优化后 trace": "after_run/trace.log"},
        notes=note,
    )
    (task_dir / "report.md").write_text(md, encoding="utf-8")
    return {"benchmark": name, "state": verdict["state"],
            "before": before["dynamic"]["total"], "after": after["dynamic"]["total"],
            "calls_before": before["dynamic"]["calls"], "calls_after": after["dynamic"]["calls"]}


def demo_arch_swap(name: str, before_arch: str, after_arch: str, opt: str, note: str) -> dict:
    """场景 C：同一源码跨架构对比（不走同架构验证闭环）。"""
    task_dir = OUT / name
    task_dir.mkdir(parents=True, exist_ok=True)
    task_id = f"m6-{name}"
    src = REPO / "benchmarks" / f"{name}.c"

    before, stats_b, trace_b, run_b = measure(src, before_arch, opt, name, task_id,
                                              task_dir / "before_rv64i")
    findings = analyze(src, stats_b, before, trace=trace_b)
    (task_dir / "findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    after, _s, _t, run_a = measure(src, after_arch, opt, name, task_id,
                                   task_dir / "after_rv64gc")
    diff = stdout_diff(run_a.stdout, run_b.stdout)

    for fname, obj in (("before_metrics.json", before), ("after_metrics.json", after)):
        (task_dir / fname).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (task_dir / "stdout_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_comparison_report(
        before, after, None,
        sources={"优化前 metrics (rv64i)": "before_metrics.json",
                 "优化后 metrics (rv64gc)": "after_metrics.json",
                 "stdout 比对": "stdout_diff.json",
                 "规则发现（rv64i 基线）": "findings.json"},
        notes=note + (
            f"输出比对：{'逐字节一致' if diff['identical'] else '存在差异（见 stdout_diff.json）'}。"
        ),
    )
    (task_dir / "report.md").write_text(md, encoding="utf-8")
    return {"benchmark": name, "state": "DIRECT-COMPARE",
            "before": before["dynamic"]["total"], "after": after["dynamic"]["total"],
            "calls_before": before["dynamic"]["calls"], "calls_after": after["dynamic"]["calls"]}


def main() -> int:
    rows = [
        demo_closed_loop(
            "b09_strlen_loop", "rv64gc", "O0",
            "benchmarks/variants/b09_strlen_loop_opt.c", "R1-hoist-strlen",
            "优化内容：R1 循环不变量外提——strlen(s) 移出循环（源码级优化，"
            "编译配置前后一致 rv64gc/-O0，仅源码不同）。"),
        demo_closed_loop(
            "b02_matrix_mul", "rv64gc", "O0",
            "benchmarks/variants/b02_matrix_mul_opt.c", "R2R7-ikj-rowptr",
            "优化内容：R2/R7——循环序 i-j-k 改 i-k-j + 行指针缓存，内层消除"
            "重复乘法寻址（整数加法结合律保证输出逐字节一致；配置前后一致）。"),
        demo_arch_swap(
            "b06_grayscale", "rv64i", "rv64gc", "O0",
            "优化内容：R9 建议\"选择带 F 扩展的架构\"——同一源码从 rv64i 切换到"
            "rv64gc，软浮点库调用被硬件浮点取代（07 文档场景 C 架构适配；同一"
            "源码，浮点语义跨 ISA 一致，golden 不变）。"),
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    (OUT / "demo_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
