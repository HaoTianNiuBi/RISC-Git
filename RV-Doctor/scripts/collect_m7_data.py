#!/usr/bin/env python3
"""M7 数据包采集：汇总冻结快照 + 补测演示关键数据 → reports/data/。

产出（全部实测，无估算；可重复运行刷新）：
  baseline_overview.md/.json   10 基准基线总表（来自 benchmarks/snapshots，不重跑）
  b06_arch_compare.md/.json    b06 在 rv32i/rv64i/rv64gc 的 R9 对比（现场补测）
  b09_opt_compare.md/.json     b09 在 O0/O2 的对比（回应"-O2 就够了"质疑）
  opt_demo_template.md         场景 A/B/C 优化前后对比表模板（variants 就绪后填充）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import run_checkup  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SNAPSHOTS = REPO / "benchmarks" / "snapshots"
DATA = REPO / "reports" / "data"

NAMES = [
    "b01_sum_array", "b02_matrix_mul", "b03_crc32", "b04_string_search",
    "b05_bubble_sort", "b06_grayscale", "b07_fib", "b08_dot_product",
    "b09_strlen_loop", "b10_window_sum",
]

CLASS_LABELS = {"integer": "整数", "memory": "访存", "branch": "分支",
                "jump": "跳转", "muldiv": "乘除", "float": "浮点", "system": "系统"}


def pct(after: float, before: float) -> str:
    if not before:
        return "—"
    return f"{(after - before) / before * 100:+.1f}%"


def write_stem(stem: str, md: str, payload: dict) -> None:
    (DATA / f"{stem}.md").write_text(md, encoding="utf-8", newline="\n")
    (DATA / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  wrote {stem}.md/.json")


def collect_overview() -> dict:
    rows, payload = [], {"source": "benchmarks/snapshots（冻结的实测 metrics.json）", "items": {}}
    for name in NAMES:
        snap_path = SNAPSHOTS / f"{name}.json"
        if not snap_path.is_file():
            print(f"  !! {name} 快照缺失，跳过（先跑 freeze_benchmarks.py）")
            continue
        m = json.loads(snap_path.read_text(encoding="utf-8"))
        payload["items"][name] = m
        d, s, e = m["dynamic"], m["static"], m["energy"]
        rows.append(
            f"| {name} | {m['meta']['arch']} | {m['meta']['opt']} "
            f"| {s['total_instructions']:,} | {d['total']:,} "
            f"| {d['by_class']['memory']:,} | {d['by_class']['branch']:,} "
            f"| {d['calls']:,} | {d['by_class']['float']:,} | {e['score']:,} |"
        )
    md = """# 10 基准基线总表（实测）

来源：`benchmarks/snapshots/`（M3 冻结的 metrics.json，经独立 Python 参考实现校验和比对）。
工具链：xPack riscv-none-elf-gcc 15.2.0 · QEMU 11.1.0 · 配置 rv64gc/O0。
动态指令数具有确定性（同一 ELF 两次运行完全一致）。

| 基准 | 架构 | 优化 | 静态指令 | 动态指令 | 访存 | 分支 | 调用 | 浮点 | 能耗估算分* |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
""" + "\n".join(rows) + """

\\* 能耗估算分 = Σ(类别动态计数 × 权重)，相对估算，仅用于优化前后趋势对比，不替代真实硬件功耗测试。
"""
    write_stem("baseline_overview", md, payload)
    return payload


def collect_b06_arch() -> dict:
    """b06 grayscale 三架构对比：RV64GC 硬件浮点 vs RV64I/RV32I 软浮点（R9 高光）。"""
    items = {}
    print("  running b06 x {rv64gc, rv64i, rv32i} @O0 ...")
    for arch in ("rv64gc", "rv64i", "rv32i"):
        r = run_checkup(REPO / "benchmarks" / "b06_grayscale.c",
                        arch=arch, opt="O0", benchmark=f"b06[{arch}]")
        assert r.state == "DONE", f"b06/{arch}: {r.state} {r.error}"
        items[arch] = {
            "dynamic_total": r.metrics["dynamic"]["total"],
            "by_class": r.metrics["dynamic"]["by_class"],
            "calls": r.metrics["dynamic"]["calls"],
            "float_insns": r.metrics["dynamic"]["by_class"]["float"],
            "energy": r.metrics["energy"]["score"],
            "static_total": r.metrics["static"]["total_instructions"],
            "toolchain": r.metrics["meta"]["toolchain"],
            "qemu": r.metrics["meta"]["qemu"],
        }
        print(f"    {arch}: dyn={items[arch]['dynamic_total']:,} "
              f"calls={items[arch]['calls']:,} float={items[arch]['float_insns']:,}")

    gc, i64, i32 = items["rv64gc"], items["rv64i"], items["rv32i"]
    md = f"""# b06 grayscale × 三架构对比（实测，R9 演示数据）

同一份浮点加权灰度程序，仅改 `-march/-mabi`。无 F/D 扩展的 RV64I/RV32I 上，
float 运算退化为 libgcc 软浮点例程（`__mulsf3`/`__addsf3`/`__floatsisf` 等）——
这就是规则 R9 的证据来源，也是"面向 RISC-V 指令特征的优化"的直观证明。

配置：-O0 · xPack GCC 15.2.0 · QEMU 11.1.0（逐条执行 trace 精确计数）。

| 指标 | RV64GC（硬浮点） | RV64I（软浮点） | RV32I（软浮点） |
|---|---:|---:|---:|
| 静态指令数 | {gc['static_total']:,} | {i64['static_total']:,} | {i32['static_total']:,} |
| 动态指令总数 | {gc['dynamic_total']:,} | {i64['dynamic_total']:,} | {i32['dynamic_total']:,} |
| 浮点类指令 | {gc['float_insns']:,} | {i64['float_insns']:,} | {i32['float_insns']:,} |
| 函数调用次数 | {gc['calls']:,} | {i64['calls']:,} | {i32['calls']:,} |
| 能耗估算分* | {gc['energy']:,} | {i64['energy']:,} | {i32['energy']:,} |

（软浮点版本的"浮点类指令"计数为 0 属预期：运算全部变成了整数指令 + 软浮点库调用，
调用开销计入 整数/跳转/访存 类别与调用次数。）

\\* 相对估算，仅用于趋势对比。
"""
    write_stem("b06_arch_compare", md, {"items": items})
    return items


def collect_b09_opt() -> dict:
    """b09 strlen_loop O0 vs O2：诚实回应"-O2 就够了"（07 文档场景 B 备选加强）。"""
    items = {}
    print("  running b09 x {O0, O2} @rv64gc ...")
    for opt in ("O0", "O2"):
        r = run_checkup(REPO / "benchmarks" / "b09_strlen_loop.c",
                        arch="rv64gc", opt=opt, benchmark=f"b09[{opt}]")
        assert r.state == "DONE", f"b09/{opt}: {r.state} {r.error}"
        items[opt] = {
            "dynamic_total": r.metrics["dynamic"]["total"],
            "by_class": r.metrics["dynamic"]["by_class"],
            "calls": r.metrics["dynamic"]["calls"],
            "energy": r.metrics["energy"]["score"],
            "static_total": r.metrics["static"]["total_instructions"],
        }
        print(f"    {opt}: dyn={items[opt]['dynamic_total']:,} calls={items[opt]['calls']:,}")

    o0, o2 = items["O0"], items["O2"]
    md = f"""# b09 strlen_loop × 编译等级对比（实测，场景 B 备选素材）

同一份"循环内 strlen"代码，仅改优化等级。用于诚实回应"-O2 编译器不就优化了吗"：
展示编译器已经做了什么（-O2 是否外提了 strlen）、源码级优化（R1 证据链）还剩什么可做。

配置：rv64gc · xPack GCC 15.2.0 · QEMU 11.1.0。

| 指标 | -O0 | -O2 | 变化率 |
|---|---:|---:|---:|
| 静态指令数 | {o0['static_total']:,} | {o2['static_total']:,} | {pct(o2['static_total'], o0['static_total'])} |
| 动态指令总数 | {o0['dynamic_total']:,} | {o2['dynamic_total']:,} | {pct(o2['dynamic_total'], o0['dynamic_total'])} |
| 函数调用次数 | {o0['calls']:,} | {o2['calls']:,} | {pct(o2['calls'], o0['calls'])} |
| 能耗估算分* | {o0['energy']:,} | {o2['energy']:,} | {pct(o2['energy'], o0['energy'])} |

演示话术要点：若 -O2 已把调用次数显著降低，说明 GCC 对内建函数做了外提——
此时强调本平台的价值是"量化告诉你编译器做了什么"；对非内建函数/指针副作用场景，
编译器无法外提，源码级优化（含 AI 建议 + 验证闭环）依然有效。

\\* 相对估算，仅用于趋势对比。
"""
    write_stem("b09_opt_compare", md, {"items": items})
    return items


def write_opt_template() -> None:
    md = """# 场景 A/B/C 优化前后对比表（模板——数字必须实测填充，禁止预估）

> 填充方式：`benchmarks/variants/<name>_opt.c` 就位后，用 `optimizer.verifier.verify`
> 跑验证闭环（产出 verdict.json + 前后 metrics.json），把 ACCEPT 案例的实测值填入。
> 变化率 = (after − before) / before × 100%。

## 场景 A：b02 矩阵乘法（R1/R2/R7 —— 循环与访存优化）

| 指标 | 优化前（实测） | 优化后（实测） | 变化率（实测） |
|---|---|---|---|
| 动态指令总数 | （待 verifier 填充） | （待填充） | （待填充） |
| 访存指令 | （待填充） | （待填充） | （待填充） |
| 分支指令 | （待填充） | （待填充） | （待填充） |
| 能耗估算分* | （待填充） | （待填充） | （待填充） |
| 输出一致性 | PASS | PASS | — |

## 场景 B：b09 strlen_loop（R1 —— 循环不变量外提，招牌戏）

证据链讲解素材（-O0 实测，来自基准基线）：
循环体内 `jal strlen` 的动态调用计数、循环执行次数、每次调用指令数
→ 参见 `reports/data/baseline_overview.md` 与 b09 任务目录的 trace_stats.json。

| 指标 | 优化前（实测） | 优化后（实测） | 变化率（实测） |
|---|---|---|---|
| 动态指令总数 | （待 verifier 填充） | （待填充） | （待填充） |
| 函数调用次数 | （待填充） | （待填充） | （待填充） |
| 能耗估算分* | （待填充） | （待填充） | （待填充） |
| 输出一致性 | PASS | PASS | — |

O0 vs O2 对照数据已就绪：`reports/data/b09_opt_compare.md`。

## 场景 C：b06 grayscale（R9 —— RV64I 软浮点高光）

三架构基线已就绪：`reports/data/b06_arch_compare.md`。
优化候选：float 权重 → 整数移位近似（`benchmarks/variants/b06_grayscale_opt.c` 就位后填充）。

| 指标（RV64I 下） | 优化前（实测） | 优化后（实测） | 变化率（实测） |
|---|---|---|---|
| 动态指令总数 | （待 verifier 填充） | （待填充） | （待填充） |
| 函数调用次数 | （待填充） | （待填充） | （待填充） |
| 能耗估算分* | （待填充） | （待填充） | （待填充） |
| 输出一致性 | PASS（近似算法需与 golden 重新约定） | PASS | — |

\\* 能耗估算分为相对估算，仅用于趋势对比，不替代真实硬件功耗测试。
"""
    (DATA / "opt_demo_template.md").write_text(md, encoding="utf-8", newline="\n")
    print("  wrote opt_demo_template.md")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    print("M7 data pack ->", DATA)
    collect_overview()
    collect_b06_arch()
    collect_b09_opt()
    write_opt_template()
    print("done.")


if __name__ == "__main__":
    main()
