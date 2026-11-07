"""trace_parser 单元测试（docs/06 §7：固定样例日志 ≥5 例）。

核心 oracle 是 tests/fixtures/smoke64_trace.log —— 真实 QEMU 11.1.0
（v11.1.0-12130）在逐条执行模式下跑手工冒烟程序产生的日志，程序为：
10 次循环（addi t0,t0,1 / blt t0,t1,loop 各执行 10 次）+ 两次半主机调用。
预期动态计数完全可手工核对，锁定格式（docs/08 风险 T1）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from profiler.trace_parser import parse_trace
from tests.conftest import TRACE_32, TRACE_64, TRACE_GCC_ANNOTATED

# ---- smoke64 程序的固定地址（见 scripts/gen_smoke_elf.py）-----------------
PC_LOOP_ADDI = 0x80000020   # addi t0,t0,1  -> 执行 10 次
PC_LOOP_BLT = 0x80000024    # blt t0,t1,loop -> 执行 10 次
PC_FIRST_SRAI = 0x80000014  # 首个半主机调用的 srai —— 实测会执行
PC_EXIT_SRAI = 0x8000003C   # 退出调用的 srai —— 实测不执行（QEMU 直接退出）
TOTAL_EXEC = 39             # 6（引导 ROM）+ 33（程序）
ROM_INSNS = 6


def test_parse_real_qemu11_log_counts():
    stats = parse_trace(TRACE_64)
    assert stats.total_exec == TOTAL_EXEC
    assert stats.exec_counts[PC_LOOP_ADDI] == 10
    assert stats.exec_counts[PC_LOOP_BLT] == 10
    assert not stats.truncated
    assert stats.unexpected_lines == 0


def test_parse_real_qemu11_log_mnemonic_map():
    stats = parse_trace(TRACE_64)
    assert stats.pc_mnemonic[PC_LOOP_ADDI] == "addi"
    # blt t0,t1 与 bgt t1,t0 同机器编码；QEMU 反汇编输出规范化的交换伪指令 bgt
    assert stats.pc_mnemonic[PC_LOOP_BLT] == "bgt"
    assert stats.pc_mnemonic[0x8000000C] == "slli"
    assert stats.pc_mnemonic[0x80000010] == "ebreak"
    assert "t0,t0,1" in stats.pc_text[PC_LOOP_ADDI]
    # 引导 ROM 的 6 条指令也在日志里（mhartid 读取等）
    assert stats.pc_mnemonic[0x1008] == "csrrs"
    # 执行过的 PC 都有助记符；未执行的 srai 不出现在 in_asm
    assert set(stats.exec_counts) <= set(stats.pc_mnemonic)
    assert PC_EXIT_SRAI not in stats.pc_mnemonic
    assert PC_FIRST_SRAI in stats.exec_counts
    assert stats.pc_index_used == 1  # QEMU 11：PC 是方括号第 2 个字段


def test_parse_32bit_log_same_format():
    stats = parse_trace(TRACE_32)
    assert stats.total_exec == TOTAL_EXEC
    assert stats.exec_counts[PC_LOOP_ADDI] == 10
    assert stats.pc_mnemonic[PC_LOOP_ADDI] == "addi"


def test_parse_empty_log(tmp_path: Path):
    p = tmp_path / "empty.log"
    p.write_text("", encoding="utf-8")
    stats = parse_trace(p)
    assert stats.total_exec == 0
    assert stats.exec_counts == {}
    assert stats.pc_mnemonic == {}
    assert not stats.truncated


def test_unexpected_lines_are_counted_not_fatal(tmp_path: Path):
    p = tmp_path / "garbage.log"
    p.write_text(
        "IN: \n"
        "0x80000000:  00128293          addi                    t0,t0,1\n"
        "Trace 0: 000001cc8cc20100 [001411ad/0000000080000000/02c24003/ff020201]\n"
        "this line means nothing\n"
        "!!!!\n",
        encoding="utf-8",
    )
    stats = parse_trace(p)
    assert stats.exec_counts == {0x80000000: 1}
    assert stats.unexpected_lines == 2


def test_truncated_log_flagged(tmp_path: Path):
    src = TRACE_64.read_bytes()
    assert len(src) > 600
    p = tmp_path / "cut.log"
    p.write_bytes(src[:600])  # 截断在日志前部
    stats = parse_trace(p, max_bytes=2**30)
    assert stats.truncated
    assert stats.total_exec < TOTAL_EXEC  # 只解析到前缀，最多是部分 Trace 行


def test_max_bytes_limit_stops_early(tmp_path: Path):
    stats = parse_trace(TRACE_64, max_bytes=800)
    assert stats.truncated
    assert stats.total_exec < TOTAL_EXEC


def test_format_adaptation_qemu8_9_style(tmp_path: Path):
    """旧版 QEMU（8.x/9.x 文档布局：方括号 3 字段、PC 在第 1 个）适配。

    布局依据历史 QEMU exec 日志格式合成（本机仅装 11.1.0，无法实测旧版）；
    解析器通过 in_asm PC 全集交叉验证自动定位 PC 字段，不硬编码版本分支。
    """
    p = tmp_path / "qemu9.log"
    lines = [
        "----------------",
        "IN: ",
        "0x0000000080000020:  00128293          addi                    t0,t0,1",
        "",
        "----------------",
        "IN: ",
        "0x0000000080000024:  fe62cee3          bgt                     t1,t0,-4",
        "",
        "Trace 0: 0x000055c000001000 [0000000080000020/0000000000000011/0000000000000000]",
        "Trace 0: 0x000055c000001000 [0000000080000024/0000000000000011/0000000000000000]",
        "Trace 0: 0x000055c000001000 [0000000080000020/0000000000000011/0000000000000000]",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats = parse_trace(p)
    assert stats.pc_index_used == 0
    assert stats.exec_counts == {0x80000020: 2, 0x80000024: 1}
    assert stats.pc_mnemonic[0x80000020] == "addi"


def test_exec_only_log_falls_back_to_layout_table(tmp_path: Path):
    """没有 IN: 段的日志（如仅 -d exec）按字段数走文档化回退表。"""
    p = tmp_path / "exec_only.log"
    p.write_text(
        "Trace 0: deadbeef [001411ad/0000000080000020/02c24003/ff020201]\n",
        encoding="utf-8",
    )
    stats = parse_trace(p)
    assert stats.pc_index_used == 1
    assert stats.exec_counts == {0x80000020: 1}


def test_missing_log_raises():
    with pytest.raises(FileNotFoundError):
        parse_trace(Path("no/such/trace.log"))


def test_parse_gcc_annotated_log():
    """真实 GCC 产物 trace：ELF 带符号表时 QEMU 给行尾附加符号名
    （`IN: __libc_init_array` / `...] __libc_init_array`），必须正确解析。

    该缺陷曾导致 pipeline 首跑 b01 只统计到 5159/95025 条（符号行全部漏计），
    此夹具锁定该格式（docs/08 风险 T1）。
    """
    stats = parse_trace(TRACE_GCC_ANNOTATED)
    assert stats.unexpected_lines == 0
    assert not stats.truncated
    assert stats.total_exec > 700          # 夹具含 713 条带符号 exec 行
    assert stats.pc_index_used == 1
    # 带符号的 PC 也必须有助记符（in_asm 与 exec 同样带注解，PC 集合一致）
    assert set(stats.exec_counts) <= set(stats.pc_mnemonic)
    # 抽一条已知注解行：__libc_init_array 首条指令（压缩编码 addi sp,sp,-32）
    assert stats.pc_mnemonic.get(0x800007B2) == "addi"
