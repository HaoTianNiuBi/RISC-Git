"""FR2：objdump 反汇编与静态统计。

统计口径（06 文档第 2 节"手工可复核"）：静态指令数 = objdump -d 输出中
代码段的可解码指令行数。所有数字来自对真实反汇编文本的解析，不做任何估算。

解析说明：
- 指令行形如 "   1000b4:\\t00a50533 \\tadd\\ta0,a0,a1"（地址、机器码十六进制、
  助记符、操作数），C 扩展指令的机器码为 4 位十六进制（2 字节）；
- ".2byte/.4byte" 等数据伪指令行与 "..." 不可解码行不计入指令；
- text_bytes = 可解码指令编码字节累加，即代码段中指令部分的大小。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from compiler.config import ToolchainError, load_config, resolve_tool

DISASM_NAME = "disasm.txt"


class DisassembleError(RuntimeError):
    """objdump 运行失败（ELF 无效、损坏等）。"""


@dataclass
class FunctionStat:
    name: str
    size_instructions: int
    section: str


@dataclass
class InsnStat:
    """单条指令（规则引擎的证据原料，M4 起使用）。"""

    addr: int  # 指令地址（PC）
    mnemonic: str
    operands: str  # 助记符之后的操作数原文（含 <sym> 注释）
    func: str | None  # 所属函数名（首个函数头之前为 None）
    section: str
    raw: str  # objdump 原始行（证据片段直接引用）


@dataclass
class StaticStats:
    total_instructions: int
    by_mnemonic: dict[str, int]
    functions: list[FunctionStat]  # name, size_instructions, section
    text_bytes: int
    insns: list[InsnStat] = field(default_factory=list)  # 指令级明细（按地址序）


_SECTION_RE = re.compile(r"^Disassembly of section (\S+):")
_FUNC_RE = re.compile(r"^([0-9a-fA-F]+)\s+<(.+)>:")
# 指令行形如 "addr:\t<machine code>\t<mnemonic>\t<operands>"；地址后必须紧跟冒号，
# 因此不会误匹配 "addr <name>:" 函数头
_ADDR_RE = re.compile(r"^\s*([0-9a-fA-F]+):\s+(.*)$")
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _split_insn_tail(tail: str) -> tuple[str, str, str] | None:
    """把 "addr:" 之后的文本切成 (机器码十六进制, 助记符, 操作数)。

    机器码是 objdump 打印的十六进制字块，宽度固定 4 或 8 位。不能用贪婪正则
    一次性匹配十六进制串：RISC-V 助记符 add 恰好全部由十六进制字符构成，
    会被误吞进机器码；按"字块宽度 4/8"逐个判定即可区分。
    """
    tokens = tail.split()
    enc: list[str] = []
    for i, tok in enumerate(tokens):
        if len(tok) in (4, 8) and set(tok) <= _HEX_CHARS:
            enc.append(tok)
        else:
            if not enc:
                return None  # 冒号后没有机器码（数据/续行等），不计入指令
            return "".join(enc), tok, " ".join(tokens[i + 1 :])
    return None  # 只有机器码没有助记符（如字面量池），不计入指令


def disassemble(elf: Path, workdir: Path) -> Path:
    """objdump -d 反汇编 ELF，落盘 workdir/disasm.txt 并返回该路径。

    objdump 不存在时抛 ToolchainError；ELF 无效等运行失败抛 DisassembleError。
    """
    objdump = resolve_tool("objdump", load_config())
    elf, workdir = Path(elf), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / DISASM_NAME

    cmd = [str(objdump), "-d", str(elf)]
    try:
        timeout = float(load_config()["timeouts"]["disasm"])
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise DisassembleError(f"objdump 超时: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise ToolchainError(f"无法启动 {objdump}: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise DisassembleError(f"objdump 失败（rc={proc.returncode}）: {tail}")
    out_path.write_bytes(proc.stdout)
    return out_path


def static_stats(disasm_path: Path) -> StaticStats:
    """解析 objdump -d 文本，返回静态统计。

    同一 objdump 版本对同一 ELF 的输出确定，因此统计结果确定可复现。
    """
    text = Path(disasm_path).read_text(encoding="utf-8", errors="replace")

    by_mnemonic: dict[str, int] = {}
    functions: list[FunctionStat] = []
    insns: list[InsnStat] = []
    total = 0
    text_bytes = 0
    section = ""
    current_func: FunctionStat | None = None

    for line in text.splitlines():
        m = _FUNC_RE.match(line)
        if m:  # 函数头在列 0，指令行有前导空格，二者不会互混
            current_func = FunctionStat(
                name=m.group(2), size_instructions=0, section=section
            )
            functions.append(current_func)
            continue
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        m = _ADDR_RE.match(line)
        if not m:
            continue
        parsed = _split_insn_tail(m.group(2))
        if parsed is None:
            continue
        encoding, mnemonic, operands = parsed
        if mnemonic.startswith("."):
            continue  # .2byte/.4byte 等数据伪指令不计入指令
        total += 1
        text_bytes += len(encoding) // 2
        by_mnemonic[mnemonic] = by_mnemonic.get(mnemonic, 0) + 1
        if current_func is not None:
            current_func.size_instructions += 1
        insns.append(
            InsnStat(
                addr=int(m.group(1), 16),
                mnemonic=mnemonic,
                operands=operands,
                func=current_func.name if current_func else None,
                section=section,
                raw=line.rstrip(),
            )
        )

    return StaticStats(
        total_instructions=total,
        by_mnemonic=by_mnemonic,
        functions=functions,
        text_bytes=text_bytes,
        insns=insns,
    )
