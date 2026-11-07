"""Metric aggregation (docs/03 §5.5, FR3): static stats + trace stats -> MetricsReport.

Every number in the report comes from toolchain ground truth (objdump static
stats, parsed QEMU trace); this module only classifies and sums. The output
dict follows the MetricsReport schema in docs/03 §6 with a few additive
diagnostic fields. It contains no timestamps or wall-clock values other than
the explicitly documented `run.wall_time_median_s` reference, so the
deterministic parts are stable across runs (docs/06 §5.4 snapshot rule).
"""
from __future__ import annotations

import hashlib
import statistics
from collections import Counter

from .trace_parser import TraceStats

try:  # step-2 module (M1); before it exists the profiler must still be testable
    from compiler.disassemble import FunctionStat, StaticStats  # type: ignore
except ImportError:  # pragma: no cover - bootstrap before M1 lands
    from dataclasses import dataclass, field

    @dataclass
    class FunctionStat:  # type: ignore[no-redef]
        name: str
        size_instructions: int
        section: str = ""

    @dataclass
    class StaticStats:  # type: ignore[no-redef]
        total_instructions: int = 0
        by_mnemonic: dict[str, int] = field(default_factory=dict)
        functions: list = field(default_factory=list)
        text_bytes: int = 0


#: 助记符 -> 类别（docs/03 §5.5：整数/访存/分支/跳转/乘除/浮点/系统其他）
MNEMONIC_CLASSES: dict[str, str] = {
    # ---- integer（显式收录：诊断字段只应报告真正未知的助记符）----------------
    **dict.fromkeys(
        "add addi addw addiw sub subi subw auipc lui "
        "sll slli sllw slliw srl srli srlw srliw sra srai sraw sraiw "
        "slt slti sltu sltiu xor xori or ori and andi not neg negw "
        "mv li nop sext.w zext.w sext.b sext.h zext.b zext.h "
        "seqz snez sltz sgtz "
        "bclri bseti binvi bexti bclrw bsetw binviw bextr "
        "sh1add sh2add sh3add sh1add.uw sh2add.uw sh3add.uw "
        "add.uw slli.uw rol ror rolw roliw rori clz clzw ctz ctzw "
        "cpop cpopw rev8 orc.b andn orn xnor min max minu maxu".split(),
        "integer",
    ),
    # ---- memory: loads / stores / atomics -----------------------------------
    **dict.fromkeys(
        "lb lbu lh lhu lw lwu ld sb sh sw sd".split()
        + "flw fsw fld fsd flh fsh".split()
        + "lr.w lr.d sc.w sc.d lr.w.aq lr.w.rl lr.d.aq lr.d.rl "
        "sc.w.aq sc.w.rl sc.d.aq sc.d.rl".split(),
        "memory",
    ),
    # ---- branch --------------------------------------------------------------
    # 注意：QEMU/objdump 会把 blt t0,t1 规范化输出为交换伪指令 bgt t1,t0（同编码），
    # 因此 bgt/ble/bgtu/bleu 必须与 blt/bge 一并归入 branch（实测 smoke 程序踩中）
    **dict.fromkeys(
        "beq bne blt bge bltu bgeu bgt ble bgtu bleu "
        "beqz bnez bltz bgez blez bgtz".split(),
        "branch",
    ),
    # ---- jump / call (objdump prints `jal ra,...` as `jal`; `jal x0` as `j`,
    #      `jalr x0,...(ra)` as `ret`/`jr` -> only jal/jalr count as calls) ----
    **dict.fromkeys("jal jalr j call tail ret jr".split(), "jump"),
    # ---- multiply / divide ---------------------------------------------------
    **dict.fromkeys(
        "mul mulh mulhsu mulhu div divu rem remu "
        "mulw divw divuw remw remuw".split(),
        "muldiv",
    ),
    # ---- float (non-load/store FP ops) ----------------------------------------
    **dict.fromkeys(
        "fadd.s fsub.s fmul.s fdiv.s fsqrt.s fsgnj.s fsgnjn.s fsgnjx.s "
        "fmin.s fmax.s fcvt.w.s fcvt.wu.s fcvt.s.w fcvt.s.wu fcvt.l.s "
        "fcvt.lu.s fcvt.s.l fcvt.s.lu fmv.w.x fmv.x.w feq.s flt.s fle.s "
        "fclass.s fadd.d fsub.d fmul.d fdiv.d fsqrt.d fsgnj.d fsgnjn.d "
        "fsgnjx.d fmin.d fmax.d fcvt.w.d fcvt.wu.d fcvt.d.w fcvt.d.wu "
        "fcvt.l.d fcvt.lu.d fcvt.d.l fcvt.d.lu fmv.d.x fmv.x.d feq.d flt.d "
        "fle.d fclass.d fmadd.s fmsub.s fnmsub.s fnmadd.s fmadd.d fmsub.d "
        "fnmsub.d fnmadd.d fcvt.s.d fcvt.d.s".split(),
        "float",
    ),
    # ---- system / other privileged or synchronization ------------------------
    **dict.fromkeys(
        "ecall ebreak wfi mret sret fence fence.i sfence.vma "
        "csrrw csrrs csrrc csrrwi csrrsi csrrci "
        "csrr csrw csrs csrc csrci csrwi csrsi".split(),
        "system",
    ),
}

#: 动态"调用"计数口径：jal 总是写 ra（jal x0 会被打印为 j/tail），jalr 写 ra
_CALL_MNEMONICS = {"jal", "jalr"}

#: metrics 中使用的类别全集（保证 schema 稳定）
CLASS_ORDER = ["integer", "memory", "branch", "jump", "muldiv", "float", "system"]

#: virt 机器 DRAM 基址；项目约定内核/程序链接于此（docs/04 §5.2）。
#: 低于该地址的执行是 QEMU 内置引导 ROM（复位向量 stub），与被分析程序无关，
#: 且实测其首条指令会偶发重复执行（同一镜像两次运行 39/40 抖动）——
#: 剔除后动态统计才满足 docs/06 §5.1 的确定性红线。剔除量计入诊断字段。
DRAM_BASE = 0x80000000


#: objdump 里以这些前缀开头、且不在上表的助记符视为浮点（含 AQ/RL 等后缀变体）
_FP_PREFIXES = (
    "fadd", "fsub", "fmul", "fdiv", "fsqrt", "fsgnj", "fmin", "fmax",
    "fcvt", "fmv", "fmadd", "fmsub", "fnmadd", "fnmsub",
    "feq", "flt", "fle", "fclass",
)

#: 原子内存操作（amo*）与保留加载/存储的前缀
_AMO_PREFIXES = ("lr", "sc", "amoadd", "amoand", "amomax", "amomaxu",
                 "amomin", "amominu", "amoor", "amoswap", "amoxor")


def classify(mnemonic: str | None) -> str:
    """Map a mnemonic to its class; unknown mnemonics fall back to integer."""
    if not mnemonic:
        return "integer"
    m = mnemonic.strip().lower()
    cls = MNEMONIC_CLASSES.get(m)
    if cls is not None:
        return cls
    # suffixed forms (AQ/RL, .w/.d variants, etc.)
    base = m.split(".")[0]
    if base in _AMO_PREFIXES:
        return "memory"
    if m.startswith(_FP_PREFIXES):
        return "float"
    return "integer"


def _function_rows(functions) -> list[dict]:
    rows = []
    for fn in functions or []:
        if isinstance(fn, dict):
            rows.append({
                "name": fn.get("name", ""),
                "instructions": fn.get("size_instructions", fn.get("instructions", 0)),
                "section": fn.get("section", ""),
            })
        else:
            rows.append({
                "name": getattr(fn, "name", ""),
                "instructions": getattr(fn, "size_instructions", 0),
                "section": getattr(fn, "section", ""),
            })
    return rows


def aggregate(static: StaticStats | dict, trace: TraceStats, run_meta: dict) -> dict:
    """Build the MetricsReport dict (docs/03 §6 schema).

    run_meta keys (all optional):
        task_id, benchmark, arch, opt, toolchain, qemu,
        stdout (str), exit_code, wall_times (list[float]),
        stdout_truncated (bool), weights (dict, overrides energy defaults)
    """
    # ---- dynamic classification -------------------------------------------
    by_class = Counter()
    unclassified: set[str] = set()
    no_mnemonic_pcs = 0
    boot_rom_insns = 0
    calls = 0
    for pc, count in trace.exec_counts.items():
        if pc < DRAM_BASE:
            # QEMU 引导 ROM（复位向量 stub）：非程序指令，且计数不确定（见 DRAM_BASE）
            boot_rom_insns += count
            continue
        mnem = trace.pc_mnemonic.get(pc)
        if mnem is None:
            # executed but never seen in IN: (should not happen with
            # one-insn-per-tb + nochain; counted for auditability)
            no_mnemonic_pcs += count
            by_class["integer"] += count
            continue
        if mnem in MNEMONIC_CLASSES:
            by_class[MNEMONIC_CLASSES[mnem]] += count
        else:
            unclassified.add(mnem)
            by_class[classify(mnem)] += count
        if mnem in _CALL_MNEMONICS:
            calls += count
    total = sum(by_class.values())
    dynamic = {"total": total, "by_class": {c: by_class.get(c, 0) for c in CLASS_ORDER},
               "calls": calls}

    # ---- static -------------------------------------------------------------
    if isinstance(static, dict):
        static_total = static.get("total_instructions", 0)
        static_by_mnemonic = static.get("by_mnemonic", {})
        static_functions = static.get("functions", [])
        text_bytes = static.get("text_bytes", 0)
    else:
        static_total = static.total_instructions
        static_by_mnemonic = static.by_mnemonic
        static_functions = static.functions
        text_bytes = static.text_bytes

    # ---- run / energy ---------------------------------------------------------
    stdout_text = run_meta.get("stdout", "") or ""
    wall_times = run_meta.get("wall_times") or []
    weights = run_meta.get("weights")

    from .energy import energy_score  # local import avoids a cycle at package init
    energy = energy_score({"dynamic": dynamic}, weights=weights)

    diagnostics = {
        "unclassified_mnemonics": sorted(unclassified),
        "exec_without_mnemonic": no_mnemonic_pcs,
        "boot_rom_instructions": boot_rom_insns,
        "trace_truncated": trace.truncated,
        "stdout_truncated": bool(run_meta.get("stdout_truncated", False)),
    }

    return {
        "meta": {
            "task_id": run_meta.get("task_id", ""),
            "toolchain": run_meta.get("toolchain", ""),
            "qemu": run_meta.get("qemu", ""),
            "arch": run_meta.get("arch", ""),
            "opt": run_meta.get("opt", ""),
            "benchmark": run_meta.get("benchmark", ""),
            "diagnostics": diagnostics,
        },
        "static": {
            "total_instructions": static_total,
            "text_bytes": text_bytes,
            "by_mnemonic": dict(static_by_mnemonic),
            "functions": _function_rows(static_functions),
        },
        "dynamic": dynamic,
        "run": {
            "stdout_sha256": hashlib.sha256(stdout_text.encode("utf-8")).hexdigest(),
            "exit_code": run_meta.get("exit_code"),
            "wall_time_median_s": statistics.median(wall_times) if wall_times else None,
        },
        "energy": energy,
    }
