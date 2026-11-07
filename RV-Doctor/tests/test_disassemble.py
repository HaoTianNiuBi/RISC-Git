"""compiler/disassemble.py 单元测试：合成样例解析 + 真实工具链集成。"""
from __future__ import annotations

from pathlib import Path

import pytest

from compiler.config import ToolchainError
from compiler.disassemble import DisassembleError, disassemble, static_stats
from tests.toolchain import requires_gcc, requires_objdump


# ---------- 不依赖工具链：解析器用固定样例锁定行为 ----------

SAMPLE_DISASM = """\
hello.elf:     file format elf64-littleriscv


Disassembly of section .init:

0000000000100328 <_init>:
   100328:\t00008067             \tret

Disassembly of section .text:

0000000000100000 <_start>:
   100000:\t3705                 \tj\t100002 <_start+0x2>

0000000000100002 <main>:
   100002:\t1101                 \taddi\tsp,sp,-32
   100004:\t00a50533             \tadd\ta0,a0,a1
   100008:\t12345678             \t.4byte\t0x12345678
   10000c:\t...                  \t...
   10000e:\t0001                 \tnop
garbage line without address
"""


def _write_sample(tmp_path: Path) -> Path:
    p = tmp_path / "disasm.txt"
    p.write_text(SAMPLE_DISASM, encoding="utf-8")
    return p


def test_static_stats_synthetic_sample(tmp_path):
    stats = static_stats(_write_sample(tmp_path))
    # .4byte 数据伪指令与 "..." 不可解码行不计入；其余 5 条全计
    assert stats.total_instructions == 5
    assert stats.by_mnemonic == {"ret": 1, "j": 1, "addi": 1, "add": 1, "nop": 1}
    # 4+2+2+4+2 字节：C 扩展指令按 2 字节、普通指令按 4 字节累加
    assert stats.text_bytes == 14
    assert [(f.name, f.section, f.size_instructions) for f in stats.functions] == [
        ("_init", ".init", 1),
        ("_start", ".text", 1),
        ("main", ".text", 3),
    ]


def test_static_stats_mnemonic_counts_sum_to_total(tmp_path):
    stats = static_stats(_write_sample(tmp_path))
    assert sum(stats.by_mnemonic.values()) == stats.total_instructions


def test_static_stats_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    stats = static_stats(p)
    assert stats.total_instructions == 0
    assert stats.by_mnemonic == {}
    assert stats.functions == []
    assert stats.text_bytes == 0


def test_static_stats_c_extension_2byte_encodings(tmp_path):
    # 全部为 C 扩展压缩指令（4 位十六进制 = 2 字节）
    p = tmp_path / "rvc.txt"
    p.write_text(
        "0000000000100000 <f>:\n"
        "   100000:\t0001                 \tnop\n"
        "   100002:\t8082                 \tret\n",
        encoding="utf-8",
    )
    stats = static_stats(p)
    assert stats.total_instructions == 2
    assert stats.text_bytes == 4
    assert stats.functions[0].size_instructions == 2


def test_disassemble_missing_objdump_raises_toolchain_error(tmp_path, monkeypatch):
    import compiler.disassemble as dd

    def boom(name, config=None):
        raise ToolchainError(f"未找到 {name}")

    monkeypatch.setattr(dd, "resolve_tool", boom)
    with pytest.raises(ToolchainError):
        dd.disassemble(tmp_path / "x.elf", tmp_path)


# ---------- 依赖工具链 ----------


@requires_objdump
def test_disassemble_invalid_elf_raises(tmp_path):
    fake = tmp_path / "fake.elf"
    fake.write_text("this is not an elf", encoding="utf-8")
    with pytest.raises(DisassembleError):
        disassemble(fake, tmp_path / "out")


@requires_gcc
@requires_objdump
def test_disassemble_and_stats_on_real_elf(hello_src, tmp_path):
    from compiler.riscv_compile import compile_source

    result = compile_source(hello_src, "rv64gc", "O0", tmp_path / "build")
    assert result.ok, f"编译失败: {result.stderr_tail}"

    disasm = disassemble(result.elf, tmp_path / "build")
    assert disasm == tmp_path / "build" / "disasm.txt"
    assert disasm.is_file()
    # objdump 会原样回显 ELF 路径；路径含中文时输出是 GBK 混合编码，
    # 与解析器一致用 errors="replace" 读取（NFR3 中文路径兼容）
    content = disasm.read_text(encoding="utf-8", errors="replace")
    assert "<main>" in content  # 04 文档 5.4 节预期：能看到 main 函数

    stats = static_stats(disasm)
    assert stats.total_instructions > 0
    assert stats.text_bytes > 0
    assert sum(stats.by_mnemonic.values()) == stats.total_instructions
    names = [f.name for f in stats.functions]
    assert "main" in names
    main_stat = next(f for f in stats.functions if f.name == "main")
    assert main_stat.size_instructions > 0
    assert sum(f.size_instructions for f in stats.functions) <= stats.total_instructions
