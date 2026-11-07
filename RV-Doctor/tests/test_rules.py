"""optimizer/rules.py 单元与集成测试（M4 DoD：每条规则 ≥1 正例 + 1 反例）。

分两层：
- 合成层（无工具链依赖）：手工构造 StaticStats/metrics/源码文本，精确锁定
  每条规则的触发与静默条件；
- 集成层（requires_gcc + qemu marker）：真实编译 → QEMU 运行 → trace →
  规则分析，验证证据字段里的动态计数来自真实执行。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from compiler.disassemble import FunctionStat, InsnStat, StaticStats
from optimizer.rules import THRESHOLDS, analyze
from tests.conftest import requires_qemu
from tests.toolchain import requires_gcc

BASE = 0x80000000  # DRAM 基址（低于它的 PC 属引导 ROM，不参与动态计数）


# ---------------------------------------------------------------------------
# 合成构造工具
# ---------------------------------------------------------------------------


def I(addr: int, mnemonic: str, operands: str, func: str | None = None) -> InsnStat:
    """一条合成指令（地址 0x80000000 起，raw 对齐 objdump 输出格式）。"""
    return InsnStat(
        addr=addr,
        mnemonic=mnemonic,
        operands=operands,
        func=func,
        section=".text",
        raw=f"  {addr:x}:\t00000000  \t{mnemonic}\t{operands}",
    )


def stats_of(insns: list[InsnStat], functions: list[str] | None = None) -> StaticStats:
    """由指令明细反推聚合字段（与 static_stats 的口径一致）。"""
    by_mnemonic: dict[str, int] = {}
    for i in insns:
        by_mnemonic[i.mnemonic] = by_mnemonic.get(i.mnemonic, 0) + 1
    if functions is None:
        functions = []
        for i in insns:
            if i.func and i.func not in functions:
                functions.append(i.func)
    fs = [
        FunctionStat(name=f, size_instructions=sum(1 for i in insns if i.func == f), section=".text")
        for f in functions
    ]
    return StaticStats(
        total_instructions=len(insns),
        by_mnemonic=by_mnemonic,
        functions=fs,
        text_bytes=4 * len(insns),
        insns=insns,
    )


def metrics_of(arch="rv64i", opt="O0", total=1000, branch=100, **extra):
    m = {
        "meta": {"arch": arch, "opt": opt},
        "dynamic": {"total": total, "by_class": {"branch": branch}, "calls": 0},
    }
    m.update(extra)
    return m


class FakeTrace:
    """最小 TraceStats 替身：只提供 exec_counts。"""

    def __init__(self, counts: dict[int, int]):
        self.exec_counts = counts


def write_src(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "t.c"
    p.write_text(text, encoding="utf-8")
    return p


def by_rule(findings: list[dict], rid: str) -> list[dict]:
    return [f for f in findings if f["rule_id"] == rid]


# ---------------------------------------------------------------------------
# R1 循环不变量重复计算
# ---------------------------------------------------------------------------


def test_r1_positive_strlen_in_loop_header(tmp_path):
    src = write_src(tmp_path, "int f(char *s){int n=0; while (n < strlen(s)) n++; return n;}\n")
    insns = [I(BASE, "jal", "8000100 <strlen>", "f")]
    fs = analyze(src, stats_of(insns), metrics_of(), trace=FakeTrace({BASE: 120}))
    r1 = by_rule(fs, "R1")
    assert len(r1) == 1
    assert r1[0]["evidence"]["callee"] == "strlen"
    assert r1[0]["evidence"]["dynamic_calls"] == 120  # 真实计数进入证据
    assert r1[0]["evidence"]["asm_snippet"].startswith(f"  {BASE:x}:")


def test_r1_negative_no_call_site_in_disasm(tmp_path):
    # 源码有 strlen( 但反汇编无调用点 → 取不到证据，必须静默
    src = write_src(tmp_path, "int f(char *s){int n=0; while (n < strlen(s)) n++; return n;}\n")
    assert by_rule(analyze(src, stats_of([]), metrics_of()), "R1") == []


def test_r1_negative_call_outside_loop(tmp_path):
    src = write_src(tmp_path, "int f(char *s){int n = strlen(s); for(int i=0;i<n;i++); return n;}\n")
    insns = [I(BASE, "jal", "8000100 <strlen>", "f")]
    assert by_rule(analyze(src, stats_of(insns), metrics_of()), "R1") == []


def test_r1_negative_cold_call_site_with_trace(tmp_path):
    # 有 trace 时执行 <2 次的调用点不算循环内调用（排除 libc 内部冷路径）
    src = write_src(tmp_path, "int f(char *s){int n=0; while (n < strlen(s)) n++; return n;}\n")
    insns = [I(BASE, "jal", "8000100 <strlen>", "f")]
    fs = analyze(src, stats_of(insns), metrics_of(), trace=FakeTrace({BASE: 1}))
    assert by_rule(fs, "R1") == []


def test_r1_negative_argument_depends_on_loop_variable(tmp_path):
    src = write_src(tmp_path, "int f(char *s){int n=0; for(int i=0;i<4;i++) n += strlen(s+i); return n;}\n")
    insns = [I(BASE, "jal", "8000100 <strlen>", "f")]
    fs = analyze(src, stats_of(insns), metrics_of(), trace=FakeTrace({BASE: 4}))
    assert by_rule(fs, "R1") == []


# ---------------------------------------------------------------------------
# R2 循环内重复读取不变内存
# ---------------------------------------------------------------------------


def _r2_insns(same: bool) -> list[InsnStat]:
    # loop: lw a0,0(a5) ... 回边 blt -> loop（含两条同址/异址 load）
    off2 = 0 if same else 8
    return [
        I(BASE + 0x00, "lw", "a0,0(a5)", "f"),
        I(BASE + 0x04, "addi", "a1,a1,1", "f"),
        I(BASE + 0x08, "lw", f"a2,{off2}(a5)", "f"),
        I(BASE + 0x0C, "add", "a3,a0,a2", "f"),
        I(BASE + 0x10, "blt", "a1,a4,80000000 <f>", "f"),
    ]


def test_r2_positive_same_address_twice_in_loop():
    fs = analyze(write_src(Path("."), ""), stats_of(_r2_insns(True)), metrics_of())
    r2 = by_rule(fs, "R2")
    assert len(r2) == 1
    assert r2[0]["evidence"]["address_pattern"] == "0(a5)"
    assert "asm_snippet" in r2[0]["evidence"]


def test_r2_negative_different_offsets():
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(_r2_insns(False)), metrics_of()), "R2") == []


def test_r2_negative_no_loop_backward_branch():
    insns = [i for i in _r2_insns(True) if i.mnemonic != "blt"] + [
        I(BASE + 0x10, "blt", "a1,a4,80000020 <f+0x20>", "f")  # 前向分支：不是循环
    ]
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(insns), metrics_of()), "R2") == []


# ---------------------------------------------------------------------------
# R3 高频函数调用（自递归 / 微小函数高频调用）
# ---------------------------------------------------------------------------


def test_r3_positive_self_recursion():
    insns = [
        I(BASE, "addi", "sp,sp,-16", "fib"),
        I(BASE + 4, "jal", f"{BASE:x} <fib>", "fib"),  # 调回自身
        I(BASE + 8, "addi", "sp,sp,16", "fib"),
        I(BASE + 12, "ret", "ra", "fib"),
    ]
    fs = analyze(write_src(Path("."), ""), stats_of(insns), metrics_of())
    r3 = by_rule(fs, "R3")
    assert len(r3) == 1 and "递归" in r3[0]["title"]


def test_r3_positive_tiny_hot_callee():
    square = [I(BASE + 0x40, "mul", "a0,a0,a0", "square"), I(BASE + 0x44, "ret", "ra", "square")]
    main = [
        I(BASE + 0x00, "addi", "a1,a1,1", "main"),
        I(BASE + 0x04, "jal", "80000040 <square>", "main"),
        I(BASE + 0x08, "blt", "a1,a4,80000000 <main>", "main"),
    ]
    call_pc = BASE + 4
    fs = analyze(
        write_src(Path("."), ""), stats_of(main + square), metrics_of(),
        trace=FakeTrace({call_pc: 500}),
    )
    r3 = by_rule(fs, "R3")
    assert len(r3) == 1
    assert r3[0]["evidence"]["callee"] == "square"
    assert r3[0]["evidence"]["dynamic_calls"] == 500


def test_r3_negative_tiny_callee_cold_or_big():
    # O2 口径：被调函数超过微小阈值(4) → 不报
    big = [I(BASE + 0x40 + 4 * k, "add", "a0,a0,a0", "big") for k in range(8)]
    main = [
        I(BASE, "addi", "a1,a1,1", "main"),
        I(BASE + 4, "jal", "80000040 <big>", "main"),
    ]
    fs = analyze(write_src(Path("."), ""), stats_of(main + big), metrics_of(opt="O2"),
                 trace=FakeTrace({BASE + 4: 500}))
    assert by_rule(fs, "R3") == []
    # O0 口径（阈值放宽到 24）：24 条以内的函数仍算微小 → 报；31 条不算 → 不报
    for size, expect in ((24, True), (31, False)):
        fn = [I(BASE + 0x40 + 4 * k, "add", "a0,a0,a0", "mid") for k in range(size)]
        main = [I(BASE + 4, "jal", "80000040 <mid>", "main")]
        fs = analyze(write_src(Path("."), ""), stats_of(main + fn), metrics_of(opt="O0"),
                     trace=FakeTrace({BASE + 4: 500}))
        assert bool(by_rule(fs, "R3")) == expect, f"O0 size={size}"
    # 调用点执行次数不足阈值 → 不报
    square = [I(BASE + 0x40, "mul", "a0,a0,a0", "square"), I(BASE + 0x44, "ret", "ra", "square")]
    main = [I(BASE + 4, "jal", "80000040 <square>", "main")]
    fs = analyze(write_src(Path("."), ""), stats_of(main + square), metrics_of(),
                 trace=FakeTrace({BASE + 4: THRESHOLDS["r3_min_site_exec"] - 1}))
    assert by_rule(fs, "R3") == []


def test_r3_negative_no_trace_no_tiny_report():
    # 无 trace 时微小函数路径必须静默（没有动态证据）
    square = [I(BASE + 0x40, "mul", "a0,a0,a0", "square"), I(BASE + 0x44, "ret", "ra", "square")]
    main = [I(BASE + 4, "jal", "80000040 <square>", "main")]
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(main + square), metrics_of()), "R3") == []


# ---------------------------------------------------------------------------
# R4 编译等级不足
# ---------------------------------------------------------------------------


def test_r4_positive_o0_vs_o2_measured():
    m = metrics_of(opt="O0", total=2000)
    m["o2_compare"] = {"dynamic_total": 1000}
    fs = analyze(write_src(Path("."), ""), stats_of([]), m)
    r4 = by_rule(fs, "R4")
    assert len(r4) == 1
    assert r4[0]["evidence"]["current_dynamic_total"] == 2000
    assert r4[0]["evidence"]["o2_dynamic_total"] == 1000
    assert r4[0]["evidence"]["ratio"] == 2.0


def test_r4_negative_cases():
    empty = stats_of([])
    # 缺 o2_compare（未实测 -O2）→ 静默
    assert by_rule(analyze(write_src(Path("."), ""), empty, metrics_of()), "R4") == []
    # 已是 O2 → 不报
    m = metrics_of(opt="O2", total=2000); m["o2_compare"] = {"dynamic_total": 1000}
    assert by_rule(analyze(write_src(Path("."), ""), empty, m), "R4") == []
    # 差距小于阈值 → 不报
    m = metrics_of(opt="O0", total=1050); m["o2_compare"] = {"dynamic_total": 1000}
    assert by_rule(analyze(write_src(Path("."), ""), empty, m), "R4") == []


# ---------------------------------------------------------------------------
# R5 冗余类型转换（sext.w）
# ---------------------------------------------------------------------------


def _r5_stats(arch_share: float) -> tuple[StaticStats, dict]:
    n_sext = max(1, int(arch_share * 100))
    insns = [I(BASE + 4 * k, "sext.w", "a0,a0" if k < n_sext else "add", "x" if k < n_sext else "y") for k in range(100)]
    # 上面构造会让 add 混进 x 函数；直接重造干净的
    insns = [I(BASE + 4 * k, "sext.w", "a0,a0", "f") for k in range(n_sext)]
    insns += [I(BASE + 0x1000 + 4 * k, "add", "a0,a0,a1", "g") for k in range(100 - n_sext)]
    return stats_of(insns), metrics_of(arch="rv64i")


def test_r5_positive_high_sextw_share():
    st, m = _r5_stats(0.10)  # 10% ≥ 4%
    fs = analyze(write_src(Path("."), ""), st, m)
    r5 = by_rule(fs, "R5")
    assert len(r5) == 1 and r5[0]["evidence"]["sext_w_count"] == 10


def test_r5_negative_low_share_or_32bit():
    st, m = _r5_stats(0.02)  # 2% < 4%
    assert by_rule(analyze(write_src(Path("."), ""), st, m), "R5") == []
    st, m = _r5_stats(0.10)
    m["meta"]["arch"] = "rv32i"  # 32 位无 sext.w 问题
    assert by_rule(analyze(write_src(Path("."), ""), st, m), "R5") == []


# ---------------------------------------------------------------------------
# R6 分支密集
# ---------------------------------------------------------------------------


def test_r6_positive_high_branch_share():
    fs = analyze(write_src(Path("."), ""), stats_of([]), metrics_of(total=1000, branch=250))
    r6 = by_rule(fs, "R6")
    assert len(r6) == 1
    assert r6[0]["evidence"]["dynamic_branch"] == 250
    assert r6[0]["evidence"]["branch_share"] == 0.25


def test_r6_negative_low_share():
    assert by_rule(
        analyze(write_src(Path("."), ""), stats_of([]), metrics_of(total=1000, branch=100)), "R6"
    ) == []


def _r6_loop_insns(branch_share_high: bool) -> list[InsnStat]:
    """8 条指令的循环：高占比版含 3 条分支（beqz 前向 ×2 + 回边 blt）。"""
    insns = [
        I(BASE + 0x00, "addi", "a1,a1,1", "main"),
        I(BASE + 0x04, "and", "a2,a2,a3", "main"),
    ]
    if branch_share_high:
        insns += [
            I(BASE + 0x08, "beqz", f"a2,{BASE + 0x30:x} <main+0x30>", "main"),
            I(BASE + 0x0C, "xor", "a4,a4,a2", "main"),
            I(BASE + 0x10, "beqz", f"a2,{BASE + 0x30:x} <main+0x30>", "main"),
            I(BASE + 0x14, "srli", "a2,a2,1", "main"),
        ]
    else:  # 低占比版：分支只有回边一条
        insns += [
            I(BASE + 0x08, "add", "a4,a4,a2", "main"),
            I(BASE + 0x0C, "mul", "a4,a4,a2", "main"),
            I(BASE + 0x10, "sub", "a2,a2,a1", "main"),
            I(BASE + 0x14, "srli", "a2,a2,1", "main"),
        ]
    insns += [
        I(BASE + 0x18, "addi", "a5,a5,1", "main"),
        I(BASE + 0x1C, "blt", f"a1,a4,{BASE:x} <main>", "main"),  # 回边
    ]
    return insns


def test_r6_positive_hot_loop_branch_dense():
    trace = FakeTrace({BASE + 4 * k: 100 for k in range(8)})
    fs = analyze(write_src(Path("."), ""), stats_of(_r6_loop_insns(True)), metrics_of(), trace=trace)
    r6 = by_rule(fs, "R6")
    assert len(r6) == 1
    ev = r6[0]["evidence"]
    assert ev["backedge_exec"] == 100
    assert ev["body_branch"] == 300  # 3 条分支 × 100 次执行（动态计数）
    assert ev["body_total"] == 800


def test_r6_negative_loop_not_branch_dense():
    trace = FakeTrace({BASE + 4 * k: 100 for k in range(8)})
    fs = analyze(write_src(Path("."), ""), stats_of(_r6_loop_insns(False)), metrics_of(), trace=trace)
    assert by_rule(fs, "R6") == []


def test_r6_negative_cold_loop():
    # 回边执行不足阈值 → 即使占比高也不报
    trace = FakeTrace({BASE + 4 * k: THRESHOLDS["r6_min_backedge"] - 1 for k in range(8)})
    fs = analyze(write_src(Path("."), ""), stats_of(_r6_loop_insns(True)), metrics_of(), trace=trace)
    assert by_rule(fs, "R6") == []


# ---------------------------------------------------------------------------
# R7 重复内存访问（近邻窗口）
# ---------------------------------------------------------------------------


def test_r7_positive_reload_pair():
    insns = [
        I(BASE, "lw", "a0,8(a1)", "f"),
        I(BASE + 4, "add", "a2,a0,a0", "f"),
        I(BASE + 8, "lw", "a3,8(a1)", "f"),  # 同址重载，间隔 2
    ]
    fs = analyze(write_src(Path("."), ""), stats_of(insns), metrics_of())
    r7 = by_rule(fs, "R7")
    assert len(r7) == 1 and r7[0]["evidence"]["gap_insns"] == 2


def test_r7_negative_store_or_call_between():
    # 中间有同址 store：重载是编译器正确行为 → 不报
    insns = [
        I(BASE, "lw", "a0,8(a1)", "f"),
        I(BASE + 4, "sw", "a2,8(a1)", "f"),
        I(BASE + 8, "lw", "a3,8(a1)", "f"),
    ]
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(insns), metrics_of()), "R7") == []
    # 中间有 call：同理不报
    insns = [
        I(BASE, "lw", "a0,8(a1)", "f"),
        I(BASE + 4, "jal", "8000100 <g>", "f"),
        I(BASE + 8, "lw", "a3,8(a1)", "f"),
    ]
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(insns), metrics_of()), "R7") == []


def test_r7_negative_too_far():
    gap = THRESHOLDS["r7_max_gap"] + 2
    insns = [I(BASE, "lw", "a0,8(a1)", "f")]
    insns += [I(BASE + 4 * k, "add", "a2,a2,a0", "f") for k in range(1, gap)]
    insns.append(I(BASE + 4 * gap, "lw", "a3,8(a1)", "f"))
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(insns), metrics_of()), "R7") == []


# ---------------------------------------------------------------------------
# R8 寄存器溢出
# ---------------------------------------------------------------------------


def _r8_insns(sp_ops: int) -> list[InsnStat]:
    insns: list[InsnStat] = []
    addr = BASE
    for k in range(6):  # 序言
        insns.append(I(addr, "addi", "sp,sp,-32", "f")); addr += 4
    for k in range(sp_ops):  # 中部：交替 sp 相对 load/store
        mn, ops = ("ld", "a0,16(sp)") if k % 2 == 0 else ("sd", "a1,24(sp)")
        insns.append(I(addr, mn, ops, "f")); addr += 4
    for k in range(6):  # 尾声
        insns.append(I(addr, "addi", "sp,sp,32", "f")); addr += 4
    return insns


def test_r8_positive_many_interior_sp_ops():
    fs = analyze(write_src(Path("."), ""), stats_of(_r8_insns(8)), metrics_of())
    r8 = by_rule(fs, "R8")
    assert len(r8) == 1
    assert r8[0]["evidence"]["interior_sp_ops"] == 8


def test_r8_evidence_uses_a_counted_stack_access():
    insns = _r8_insns(8)
    insns.insert(6, I(BASE + 0x1000, "lw", "a0,0(a1)", "f"))
    fs = analyze(write_src(Path("."), ""), stats_of(insns), metrics_of())
    r8 = by_rule(fs, "R8")
    assert len(r8) == 1
    assert "(sp)" in r8[0]["evidence"]["asm_snippet"]


def test_r8_negative_below_threshold():
    assert by_rule(analyze(write_src(Path("."), ""), stats_of(_r8_insns(4)), metrics_of()), "R8") == []


# ---------------------------------------------------------------------------
# R9 软浮点退化
# ---------------------------------------------------------------------------


def test_r9_positive_rv64i_mulsf3():
    insns = [I(BASE, "jal", "8000100 <__mulsf3>", "main")]
    fs = analyze(write_src(Path("."), ""), stats_of(insns), metrics_of(arch="rv64i"))
    r9 = by_rule(fs, "R9")
    assert len(r9) == 1 and r9[0]["evidence"]["callee"] == "__mulsf3"


def test_r9_negative_hard_float_arch():
    insns = [I(BASE, "jal", "8000100 <__mulsf3>", "main")]
    assert by_rule(
        analyze(write_src(Path("."), ""), stats_of(insns), metrics_of(arch="rv64gc")), "R9"
    ) == []


def test_r9_negative_long_double_not_user_regression():
    # tf 家族（128 位长双精度）与 newlib __multadd 属工具链常态，不得报
    insns = [
        I(BASE, "jal", "8000100 <__trunctfdf2>", "main"),
        I(BASE + 4, "jal", "8000200 <__multadd>", "main"),
    ]
    assert by_rule(
        analyze(write_src(Path("."), ""), stats_of(insns), metrics_of(arch="rv64i")), "R9"
    ) == []


# ---------------------------------------------------------------------------
# schema 与排序
# ---------------------------------------------------------------------------


def test_finding_schema_four_elements(tmp_path):
    src = write_src(tmp_path, "int f(char *s){int n=0; while (n < strlen(s)) n++; return n;}\n")
    insns = [I(BASE, "jal", "8000100 <strlen>", "f")]
    fs = analyze(src, stats_of(insns), metrics_of())
    assert fs, "用例应至少产出一条发现"
    for f in fs:
        assert set(f) >= {"id", "source", "rule_id", "title", "location",
                          "evidence", "suggestion", "expected_gain", "risk", "confidence"}
        assert f["evidence"], "证据字段不得为空"
        assert 0.0 < f["confidence"] <= 1.0
        assert f["location"]["scope"] in ("user", "library")


def test_user_scope_sorted_before_library(tmp_path):
    src = write_src(tmp_path, "int f(char *s){int n=0; while (n < strlen(s)) n++; return n;}\n")
    user = [I(BASE, "jal", "8000100 <strlen>", "f")]
    lib = [I(BASE + 0x100, "lw", "a0,0(s0)", "_vfprintf_r"),
           I(BASE + 0x104, "lw", "a1,0(s0)", "_vfprintf_r")]
    fs = analyze(src, stats_of(user + lib), metrics_of())
    scopes = [f["location"]["scope"] for f in fs]
    assert scopes == sorted(scopes, key=lambda s: 0 if s == "user" else 1)


# ---------------------------------------------------------------------------
# 集成层：真实工具链 + QEMU
# ---------------------------------------------------------------------------

FLOAT_C = """\
#include <stdio.h>
int main(void) {
    float w0 = 0.299f, w1 = 0.587f;
    unsigned char px[6] = {10, 20, 30, 40, 50, 60};
    float acc = 0.0f;
    for (int i = 0; i < 6; i += 2)
        acc += w0 * px[i] + w1 * px[i + 1];
    printf("gray=%d\\n", (int)acc);
    return 0;
}
"""


@pytest.fixture
def _chain(workdir: Path):
    """真实链路夹具：编译 → 运行 → 解析 → 聚合，返回 (src, stats, metrics, trace)。"""
    from compiler.disassemble import disassemble, static_stats
    from compiler.riscv_compile import compile_source
    from profiler.metrics import aggregate
    from profiler.runner import run_baremetal
    from profiler.trace_parser import parse_trace

    def run(src_text: str, arch: str, opt: str):
        src = workdir / "a.c"
        src.write_text(src_text, encoding="utf-8")
        r = compile_source(src, arch, opt, workdir)
        assert r.ok, r.stderr_tail
        rr = run_baremetal(r.elf, workdir)
        assert rr.ok, (rr.exit_code, rr.stderr_tail)
        stats = static_stats(disassemble(r.elf, workdir))
        trace = parse_trace(rr.trace_path)
        metrics = aggregate(stats, trace, {
            "arch": arch, "opt": opt, "stdout": rr.stdout, "exit_code": rr.exit_code,
        })
        return src, stats, metrics, trace

    return run


@requires_gcc
@requires_qemu
@pytest.mark.qemu
def test_integration_hello_rv64gc_no_r9_user_findings_present(_chain):
    src, stats, metrics, trace = _chain(
        "#include <stdio.h>\nint main(void){int s=0; for(int i=1;i<=100;i++) s+=i;"
        " printf(\"sum=%d\\n\",s); return 0;}",
        "rv64gc", "O0",
    )
    fs = analyze(src, stats, metrics, trace=trace)
    assert by_rule(fs, "R9") == []  # 硬浮点架构不得报软浮点
    assert any(f["location"]["scope"] == "user" for f in fs) or fs == []


@requires_gcc
@requires_qemu
@pytest.mark.qemu
def test_integration_float_rv64i_r9_with_real_counts(_chain):
    src, stats, metrics, trace = _chain(FLOAT_C, "rv64i", "O0")
    r9 = by_rule(analyze(src, stats, metrics, trace=trace), "R9")
    assert r9, "rv64i 浮点程序必须触发 R9"
    for f in r9:
        assert "dynamic_calls" in f["evidence"], "集成路径的 R9 必须带真实动态计数"
        assert f["evidence"]["dynamic_calls"] >= 1
