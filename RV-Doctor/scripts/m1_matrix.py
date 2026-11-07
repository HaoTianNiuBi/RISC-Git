# -*- coding: utf-8 -*-
"""M1 DoD 证据生成器：2 程序 × 3 架构 × 5 优化等级 = 30 组
编译 → 反汇编 → 静态统计，全部产物落盘 workdir/m1_matrix/。

用法：.venv/Scripts/python.exe scripts/m1_matrix.py
产物：workdir/m1_matrix/<prog>-<arch>-<opt>/{app.elf, build.log, disasm.txt, stats.json}
      workdir/m1_matrix/matrix_summary.json
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
from tests.toolchain import CALLS_C, HELLO_C  # noqa: E402

PROGRAMS = {"hello": HELLO_C, "calls": CALLS_C}
ARCHS = ["rv32i", "rv64i", "rv64gc"]
OPTS = ["O0", "O1", "O2", "O3", "Os"]

OUT = REPO / "workdir" / "m1_matrix"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    t0 = time.perf_counter()

    for prog, code in PROGRAMS.items():
        src = OUT / f"{prog}.c"
        src.write_text(code, encoding="utf-8")
        for arch in ARCHS:
            for opt in OPTS:
                name = f"{prog}-{arch}-{opt}"
                case = OUT / name
                result = compile_source(src, arch, opt, case)
                row = {"case": name, "compile_ok": result.ok}
                if result.ok:
                    disasm = disassemble(result.elf, case)
                    stats = static_stats(disasm)
                    row.update(
                        total_instructions=stats.total_instructions,
                        text_bytes=stats.text_bytes,
                        functions=len(stats.functions),
                        main_insns=next(
                            (f.size_instructions for f in stats.functions if f.name == "main"),
                            None,
                        ),
                    )
                else:
                    row["stderr_tail"] = result.stderr_tail[:200]
                rows.append(row)
                print(("PASS" if result.ok else "FAIL"), name, flush=True)

    summary = {
        "toolchain": "xpack riscv-none-elf-gcc 15.2.0 (binutils 2.45)",
        "qemu": "11.1.0",
        "cases": len(rows),
        "passed": sum(1 for r in rows if r["compile_ok"]),
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "results": rows,
    }
    (OUT / "matrix_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n{summary['passed']}/{summary['cases']} passed in {summary['elapsed_s']}s")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
