#!/usr/bin/env python3
"""M5 端到端演示：两个真实基准走通「体检 → 规则取证 → 候选修改 → 验证闭环」。

基准 1（b09 式 strlen_loop）：循环不变量 strlen —— 规则 R1 应带真实证据命中，
候选外提 strlen 后动态指令应显著下降（ACCEPTED）。
基准 2（square 微小函数高频调用）：候选内联后调用消失（ACCEPTED）。

所有数字均来自 compile_source / run_baremetal / parse_trace / rules.analyze
的真实产物；本脚本只是串联，不生成任何数字。

用法：.venv/Scripts/python.exe scripts/m5_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from compiler.disassemble import disassemble, static_stats
from compiler.riscv_compile import compile_source
from optimizer.rules import analyze
from optimizer.verifier import verify
from profiler.metrics import aggregate
from profiler.runner import run_baremetal
from profiler.trace_parser import parse_trace

WORK = REPO / "workdir" / "m5_demo"

STRLEN_LOOP = """#include <stdio.h>
#include <string.h>

static char text[64];

int count_char(const char *s, char c) {
    int count = 0;
    for (size_t i = 0; i < strlen(s); i++) {  /* 可优化点：循环不变量 strlen */
        if (s[i] == c) {
            count++;
        }
    }
    return count;
}

int main(void) {
    strcpy(text, "riscv-riscv-riscv-riscv");
    int n = count_char(text, 'i');
    printf("count=%d\\n", n);
    return 0;
}
"""

STRLEN_LOOP_OPT = """#include <stdio.h>
#include <string.h>

static char text[64];

int count_char(const char *s, char c) {
    int count = 0;
    size_t len = strlen(s);                   /* 优化：不变量外提 */
    for (size_t i = 0; i < len; i++) {
        if (s[i] == c) {
            count++;
        }
    }
    return count;
}

int main(void) {
    strcpy(text, "riscv-riscv-riscv-riscv");
    int n = count_char(text, 'i');
    printf("count=%d\\n", n);
    return 0;
}
"""

CALLS_SRC = """#include <stdio.h>

static int square(int x) {
    return x * x;
}

int main(void) {
    int acc = 0;
    for (int i = 1; i <= 20; i++) {
        acc += square(i);                     /* 可优化点：微小函数高频调用 */
    }
    printf("acc=%d\\n", acc);
    return 0;
}
"""

CALLS_OPT = """#include <stdio.h>

int main(void) {
    int acc = 0;
    for (int i = 1; i <= 20; i++) {
        acc += i * i;                         /* 优化：内联微小函数 */
    }
    printf("acc=%d\\n", acc);
    return 0;
}
"""


def baseline_of(name: str, src_text: str) -> tuple[dict, Path]:
    """编译→运行→统计→规则分析，构造 verify() 的 baseline（pipeline 的 M5 雏形）。"""
    bdir = WORK / name
    bdir.mkdir(parents=True, exist_ok=True)
    src = bdir / "src.c"
    src.write_text(src_text, encoding="utf-8")

    cres = compile_source(src, "rv64i", "O0", bdir)
    assert cres.ok, f"{name} 编译失败: {cres.stderr_tail}"
    rres = run_baremetal(cres.elf, bdir)
    assert rres.ok, f"{name} 运行失败: {rres.stderr_tail}"
    static = static_stats(disassemble(cres.elf, bdir))
    trace = parse_trace(rres.trace_path)
    metrics = aggregate(
        static, trace,
        {"task_id": f"demo-{name}", "arch": "rv64i", "opt": "O0",
         "toolchain": "xpack gcc 15.2.0", "qemu": "qemu 11.1.0",
         "stdout": rres.stdout, "exit_code": rres.exit_code},
    )
    findings = analyze(src, static, metrics, trace)
    baseline = {
        "stdout": rres.stdout,
        "metrics": metrics,
        "arch": "rv64i",
        "opt": "O0",
        "task_id": f"demo-{name}",
        "suggestion_id": f"{name}-opt",
        "static_stub": {
            "total_instructions": static.total_instructions,
            "by_mnemonic": static.by_mnemonic,
            "functions": static.functions,
            "text_bytes": static.text_bytes,
        },
        "meta": {"arch": "rv64i", "opt": "O0",
                  "toolchain": "xpack gcc 15.2.0", "qemu": "qemu 11.1.0"},
    }
    return baseline, src, findings, metrics


def show(name: str, src_text: str, opt_text: str) -> None:
    print(f"\n{'=' * 64}\n基准 {name}\n{'=' * 64}")
    baseline, src, findings, metrics = baseline_of(name, src_text)
    d = metrics["dynamic"]
    print(f"[体检] 动态指令 {d['total']} | 分支 {d['by_class']['branch']} | "
          f"访存 {d['by_class']['memory']} | 调用 {d['calls']}")
    print(f"[规则] {len(findings)} 条发现：")
    for f in findings:
        ev = f.get("evidence") or {}
        print(f"  - [{f['rule_id']}] {f['title']}（置信度 {f['confidence']:.0%}，"
              f"证据: {str(ev)[:90]}…）")

    cand = WORK / name / "candidate.c"
    cand.write_text(opt_text, encoding="utf-8")
    verdict = verify(cand, baseline, WORK)
    print(f"[验证] {verdict['state']}  轨迹 {'→'.join(verdict['trace'])}")
    print(f"       动态指令 {verdict['before']['dynamic.total']} → "
          f"{verdict['after']['dynamic.total']}"
          f"（{verdict['additive'].get('improvement', {}).get('instr_delta', 0):+d}）"
          f"  能耗分 {verdict['before']['energy.score']} → {verdict['after']['energy.score']}")
    if verdict["state"] != "ACCEPTED":
        print(f"       失败原因: {verdict['failure_reason']}")
        sys.exit(1)


if __name__ == "__main__":
    show("b09_strlen_loop", STRLEN_LOOP, STRLEN_LOOP_OPT)
    show("calls_inline", CALLS_SRC, CALLS_OPT)
    print(f"\nM5 端到端演示完成：两个基准均 ACCEPTED，产物见 {WORK}")
