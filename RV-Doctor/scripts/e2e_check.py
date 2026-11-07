#!/usr/bin/env python3
"""项目业务全链路巡检（端到端，等价于用户在界面上完成的完整流程）：

    基准源码 → run_checkup（编译→反汇编→QEMU 运行→trace→指标→确定性校验）
            → 快照回归比对（与 benchmarks/snapshots 冻结值必须一致）
            → rules.analyze（规则取证）
            → 官方优化候选（benchmarks/variants/*_opt.c）→ verifier.verify（验证闭环）
            → render_comparison_report（Markdown 对比报告）

所有数字来自真实工具链产物，本脚本只串联、不生成数字。
用法：.venv/Scripts/python.exe -X utf8 scripts/e2e_check.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from compiler.disassemble import static_stats
from optimizer.rules import analyze
from optimizer.verifier import verify
from pipeline import run_checkup
from profiler.trace_parser import parse_trace
from reports.report import render_comparison_report

ARCH, OPT = "rv64gc", "O0"
WORKROOT = REPO / "workdir" / "e2e"
BENCH = REPO / "benchmarks"
CHECK_LIST = ["b09_strlen_loop", "b02_matrix_mul", "b06_grayscale", "b07_fib"]

summary: list[str] = []
issues: list[str] = []

for name in CHECK_LIST:
    t0 = time.perf_counter()
    print(f"\n=== {name} ===")
    src = BENCH / f"{name}.c"
    r = run_checkup(src, arch=ARCH, opt=OPT, benchmark=name,
                    workroot=WORKROOT, verify_determinism=True)
    if r.state != "DONE":
        issues.append(f"{name}: 体检失败 state={r.state} error={r.error}")
        print(f"  [体检] FAIL state={r.state}: {r.error}")
        continue
    m = r.metrics
    d = m["dynamic"]
    wall = time.perf_counter() - t0
    print(f"  [体检] DONE 动态指令 {d['total']} | 调用 {d['calls']} | "
          f"{wall:.1f}s | {r.workdir.name}")

    # ---- 快照回归：动态指令数必须与冻结值一致（确定性红线）
    snap = json.loads((BENCH / "snapshots" / f"{name}.json").read_text(encoding="utf-8"))
    if snap["dynamic"]["total"] != d["total"]:
        issues.append(f"{name}: 快照不一致 冻结 {snap['dynamic']['total']} vs 实测 {d['total']}")
        print(f"  [快照] FAIL 冻结 {snap['dynamic']['total']} vs 实测 {d['total']}")
    else:
        print(f"  [快照] 一致 ✓（{d['total']}）")

    # ---- 规则取证（用户代码优先展示）
    static = static_stats(r.workdir / "disasm.txt")
    trace = parse_trace(r.workdir / "trace.log")
    findings = analyze(src, static, m, trace)
    user_f = [f for f in findings if f.get("location", {}).get("scope") == "user"]
    print(f"  [规则] {len(findings)} 条发现（用户代码 {len(user_f)}）：")
    for f in user_f[:3]:
        print(f"    - [{f['rule_id']}] {f['title']}（置信度 {f['confidence']:.0%}）")

    # ---- 优化候选验证闭环（有官方 variant 的基准）
    variant = BENCH / "variants" / f"{name}_opt.c"
    if not variant.exists():
        print("  [闭环] 无官方候选，跳过")
        continue
    baseline = {
        "stdout": r.stdout,
        "metrics": m,
        "arch": ARCH,
        "opt": OPT,
        "task_id": m["meta"]["task_id"],
        "suggestion_id": f"{name}-variant",
        "static_stub": {
            "total_instructions": static.total_instructions,
            "by_mnemonic": static.by_mnemonic,
            "functions": static.functions,
            "text_bytes": static.text_bytes,
        },
        "meta": {"arch": ARCH, "opt": OPT,
                  "toolchain": m["meta"]["toolchain"], "qemu": m["meta"]["qemu"]},
    }
    verdict = verify(variant, baseline, WORKROOT)
    if verdict["state"] != "ACCEPTED":
        issues.append(f"{name}: 闭环 {verdict['state']} — {verdict['failure_reason']}")
        print(f"  [闭环] {verdict['state']}: {verdict['failure_reason']}")
        continue
    after_metrics = json.loads((Path(verdict['candidate_dir']) / 'metrics.json').read_text(encoding='utf-8')) \
        if (Path(verdict['candidate_dir']) / 'metrics.json').exists() else None
    print(f"  [闭环] ACCEPTED 动态指令 {verdict['before']['dynamic.total']} → "
          f"{verdict['after']['dynamic.total']}（{verdict['additive']['improvement']['instr_delta']:+d}）")
    summary.append(f"{name}: {verdict['before']['dynamic.total']} → {verdict['after']['dynamic.total']}")

    # ---- 对比报告
    if after_metrics is not None:
        report_md = render_comparison_report(m, after_metrics, verdict)
    else:
        # verifier 未单独落 metrics.json 时，用 verdict 中的 after 汇总生成精简注记
        report_md = render_comparison_report(m, m, verdict,
                                             notes="after 指标未单独落盘，此报告仅含 verdict 结论")
    out = WORKROOT / "reports" / f"{name}_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report_md, encoding="utf-8")
    print(f"  [报告] {out.relative_to(REPO)}")

print("\n" + "=" * 60)
print("闭环结果：" + ("；".join(summary) if summary else "（无）"))
if issues:
    print(f"\n发现问题 {len(issues)} 个：")
    for i in issues:
        print(f"  ✗ {i}")
    sys.exit(1)
print("\n全链路巡检通过 ✓")
