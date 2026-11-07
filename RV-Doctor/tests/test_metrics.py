"""metrics / energy 单元测试（docs/06 §7：分类映射、聚合、权重快照）。"""
from __future__ import annotations

import hashlib

from profiler.energy import DEFAULT_WEIGHTS, energy_score
from profiler.metrics import CLASS_ORDER, MNEMONIC_CLASSES, aggregate, classify
from profiler.trace_parser import parse_trace
from tests.conftest import TRACE_64


# ---- classify：代表性助记符全覆盖 -------------------------------------------
def test_classify_representative_mnemonics():
    cases = {
        # 整数（含伪指令）
        "add": "integer", "addi": "integer", "lui": "integer", "auipc": "integer",
        "mv": "integer", "li": "integer", "nop": "integer", "sext.w": "integer",
        "slli": "integer", "srai": "integer",
        # 访存
        "lw": "memory", "ld": "memory", "sd": "memory", "lbu": "memory",
        "flw": "memory", "fsd": "memory", "lr.w": "memory", "sc.w": "memory",
        "amoadd.w": "memory",
        # 分支（含伪指令）
        "beq": "branch", "bne": "branch", "blt": "branch", "beqz": "branch",
        "bgez": "branch",
        # 跳转
        "jal": "jump", "jalr": "jump", "j": "jump",
        # 乘除
        "mul": "muldiv", "mulh": "muldiv", "div": "muldiv", "remu": "muldiv",
        "divw": "muldiv",
        # 浮点
        "fadd.s": "float", "fmul.d": "float", "fcvt.w.s": "float",
        "fmadd.d": "float", "fmv.x.w": "float",
        # 系统
        "ecall": "system", "ebreak": "system", "csrrs": "system",
        "fence": "system", "mret": "system", "wfi": "system",
    }
    for mnem, want in cases.items():
        assert classify(mnem) == want, mnem


def test_classify_is_total_and_case_insensitive():
    assert classify("FOO.BAR") == "integer"   # 未知助记符兜底为 integer
    assert classify("") == "integer"
    assert classify(None) == "integer"
    assert classify("ADDI") == "integer"
    assert classify("FADD.S") == "float"
    # fence 以 f 开头但不是浮点
    assert classify("fence.i") == "system"


def test_mnemonic_table_has_all_classes():
    present = set(MNEMONIC_CLASSES.values())
    # 七类全部显式入表（integer 从兜底升级为显式映射后，诊断只报真正未知的助记符）
    assert present == set(CLASS_ORDER)


# ---- aggregate：真实 trace 夹具 + 手工静态统计 --------------------------------
def _fake_static():
    """smoke64 程序的静态统计（手工编码程序，指令已知，共 17 条）。"""
    from compiler.disassemble import FunctionStat, StaticStats

    return StaticStats(
        total_instructions=17,
        by_mnemonic={"auipc": 2, "addi": 6, "mv": 1, "slli": 2, "ebreak": 2,
                     "srai": 2, "blt": 1, "jal": 1},
        functions=[FunctionStat(name="_start", size_instructions=17, section=".text")],
        text_bytes=68,
    )


def test_aggregate_real_trace_schema_and_values():
    trace = parse_trace(TRACE_64)
    report = aggregate(
        _fake_static(),
        trace,
        run_meta={
            "task_id": "t-test-001",
            "benchmark": "smoke64",
            "arch": "rv64i",
            "opt": "O0",
            "toolchain": "hand-assembled",
            "qemu": "11.1.0",
            "stdout": "rv-doctor smoke: loop done\n",
            "exit_code": 0,
        },
    )
    # schema：五大数据块齐全
    assert set(report) == {"meta", "static", "dynamic", "run", "energy"}
    assert set(report["dynamic"]) == {"total", "by_class", "calls"}
    assert set(report["dynamic"]["by_class"]) == set(CLASS_ORDER)
    # 动态口径 = DRAM 区（程序）执行：39 条 Trace 中 6 条是引导 ROM，剔除后 33
    assert trace.total_exec == 39
    assert report["dynamic"]["total"] == 33
    assert sum(report["dynamic"]["by_class"].values()) == 33
    assert report["meta"]["diagnostics"]["boot_rom_instructions"] == 6
    # 类别抽查：blt ×10 -> branch；ebreak ×2 -> system（ROM 的 csrrs 已随 ROM 剔除）
    assert report["dynamic"]["by_class"]["branch"] == 10
    assert report["dynamic"]["by_class"]["system"] == 2
    # 无 jal/jalr -> calls = 0
    assert report["dynamic"]["calls"] == 0
    # 静态块原样转录
    assert report["static"]["total_instructions"] == 17
    assert report["static"]["functions"][0]["name"] == "_start"
    # run 块
    assert report["run"]["exit_code"] == 0
    assert report["run"]["stdout_sha256"] == hashlib.sha256(
        "rv-doctor smoke: loop done\n".encode()
    ).hexdigest()
    assert report["run"]["wall_time_median_s"] is None
    # 能耗块
    assert report["energy"]["weights_used"] == DEFAULT_WEIGHTS
    assert report["energy"]["score"] == sum(
        report["dynamic"]["by_class"][c] * DEFAULT_WEIGHTS[c] for c in CLASS_ORDER
    )
    assert "免责" in report["energy"]["disclaimer"] or "相对" in report["energy"]["disclaimer"]


def test_aggregate_is_deterministic():
    trace = parse_trace(TRACE_64)
    meta = {"task_id": "t", "stdout": "x", "exit_code": 0}
    r1 = aggregate(_fake_static(), trace, meta)
    r2 = aggregate(_fake_static(), trace, meta)
    assert r1 == r2  # 无时间戳字段，整份 JSON 逐字节可复现


def test_aggregate_calls_count_jal_jalr_only():
    """calls 口径：jal/jalr 计入；ret/jr（jalr x0 的伪指令形式）不计入。"""
    from profiler.trace_parser import TraceStats

    base = 0x80000000  # PC 须在 DRAM 区，否则会被当引导 ROM 剔除
    trace = TraceStats(
        pc_mnemonic={base + 0x100: "jal", base + 0x104: "jalr", base + 0x108: "ret",
                     base + 0x10C: "jr", base + 0x110: "j", 0x1000: "auipc"},
        exec_counts={base + 0x100: 3, base + 0x104: 2, base + 0x108: 5,
                     base + 0x10C: 1, base + 0x110: 1, 0x1000: 1},
    )
    report = aggregate({"total_instructions": 0, "by_mnemonic": {}, "functions": [],
                        "text_bytes": 0},
                       trace, {"stdout": "", "exit_code": 0})
    assert report["dynamic"]["calls"] == 5          # jal+jalr；ret/jr 不算调用
    assert report["dynamic"]["by_class"]["jump"] == 12  # 全部 5 种助记符都是 jump
    assert report["dynamic"]["total"] == 12         # ROM 的 auipc ×1 被剔除
    assert report["meta"]["diagnostics"]["boot_rom_instructions"] == 1
    assert report["meta"]["diagnostics"]["unclassified_mnemonics"] == []


# ---- energy -------------------------------------------------------------------
def test_energy_hand_computed():
    metrics = {"dynamic": {"by_class": {"integer": 10, "memory": 2}}}
    got = energy_score(metrics)
    assert got["score"] == 10 * 1.0 + 2 * 3.0
    assert got["weights_used"] == DEFAULT_WEIGHTS
    assert got["disclaimer"]


def test_energy_custom_weights_are_snapshotted():
    custom = dict(DEFAULT_WEIGHTS, memory=10.0)
    got = energy_score({"dynamic": {"by_class": {"memory": 2}}}, weights=custom)
    assert got["score"] == 20
    assert got["weights_used"]["memory"] == 10.0


def test_energy_empty_and_unknown_class():
    assert energy_score({})["score"] == 0
    # 未知类别按 integer 权重兜底，不会把已执行的指令从分值里丢掉
    got = energy_score({"dynamic": {"by_class": {"bizarre": 4}}})
    assert got["score"] == 4 * DEFAULT_WEIGHTS["integer"]
