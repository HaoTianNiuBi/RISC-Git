"""QEMU trace log parser (docs/03 §5.4, FR3; risk T1 adaptation layer).

Parses `-d in_asm,exec,nochain` logs produced with per-insn execution enabled
(`-accel tcg,one-insn-per-tb=on` / `-one-insn-per-tb` / `-singlestep`), where
every TB holds exactly one instruction, so:

* every ``IN:`` section translates one instruction -> ``0x<pc>:  <hex>  <mnemonic> ...``
* every ``Trace ...: ... [f0/f1/f2/f3]`` line is one executed instruction

The guest PC field's position inside the ``[...]`` bracket list has changed
across QEMU versions.  Rather than hard-coding an offset per version, the
parser *auto-locates* it: with one-insn-per-tb + nochain every executed PC has
an ``IN:`` section, so the correct field is the bracket field whose values all
appear in the ``IN:`` PC set.  A documented fallback table handles logs without
``IN:`` sections.  Version upgrades therefore only touch this module (docs/08 T1).

Parsing streams the file line by line with a byte cap (`max_bytes`), so
multi-GB trace logs never load fully into memory.  Statistics depend only on
log content -> deterministic (docs/06 §5.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ``Trace 0: 000001cc8cc20100 [001411ad/0000000000001000/02c24003/ff020201]``
# （旧版 QEMU 在宿主地址字段带 0x 前缀，一并容忍；GCC 产物带符号表时行尾
# 会附加符号名，如 `...] _exit`，同样容忍）
_EXEC_RE = re.compile(
    r"^Trace\s+\d+:\s+(?:0x)?[0-9a-fA-F]+\s+\[((?:0x)?[0-9a-fA-F]+"
    r"(?:/(?:0x)?[0-9a-fA-F]+)*)\].*$"
)
# ``0x80000020:  00128293          addi                    t0,t0,1``
_IN_ASM_RE = re.compile(
    r"^\s*0x([0-9a-fA-F]+):\s+([0-9a-fA-F]+)\s+([A-Za-z][A-Za-z0-9._]*)\s*(.*?)\s*$"
)

#: bracket layouts by field count for logs lacking IN: sections
#: (QEMU 11.x prints 4 fields with PC at index 1; older 8/9.x 3 fields PC at 0)
_FALLBACK_PC_INDEX = {4: 1, 3: 0}

#: structural lines that are expected but carry no data
#: （IN:/OUT: 段头在 ELF 带符号表时形如 "IN: _exit"）
_NOISE_LINE_RE = re.compile(r"^(IN:|OUT:)(\s.*)?$|-+")


@dataclass
class TraceStats:
    pc_mnemonic: dict[int, str] = field(default_factory=dict)   # IN: 段：PC -> 助记符
    exec_counts: dict[int, int] = field(default_factory=dict)   # exec 段：PC -> 执行次数
    # -- additive fields beyond the documented schema ------------------------
    pc_text: dict[int, str] = field(default_factory=dict)  # PC -> 完整反汇编文本（供规则引擎用）
    truncated: bool = False            # 超过 max_bytes，只解析了前缀
    unexpected_lines: int = 0          # 既非已知格式也非结构线的行（诊断用）
    pc_index_used: int | None = None   # exec 方括号中 PC 字段的下标（适配层结果）

    @property
    def total_exec(self) -> int:
        return sum(self.exec_counts.values())


class _LimitedLineReader:
    """Streams a log file as decoded lines, capping total bytes consumed.

    `truncated` is True when iteration stopped early due to `max_bytes`, or
    when the file does not end with a newline (a complete QEMU log always
    terminates its last line — a missing terminator means the log was cut
    mid-write, e.g. QEMU killed on timeout). Binary mode keeps byte
    accounting exact and sidesteps newline translation differences.
    """

    def __init__(self, log_path: Path, max_bytes: int) -> None:
        self._path = log_path
        self._max_bytes = max_bytes
        self.truncated = False
        self._last_byte = b""

    def __iter__(self):
        consumed = 0
        with open(self._path, "rb") as f:
            for raw in f:
                consumed += len(raw)
                if consumed > self._max_bytes:
                    self.truncated = True
                    return
                self._last_byte = raw[-1:]
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if self._last_byte not in (b"", b"\n"):
            self.truncated = True


def parse_trace(log_path: Path, max_bytes: int = 2**30) -> TraceStats:
    """Parse a QEMU in_asm+exec trace log into TraceStats.

    Deterministic: the result depends only on the log bytes (docs/06 §5.1).
    """
    log_path = Path(log_path)
    if not log_path.exists():
        raise FileNotFoundError(str(log_path))

    stats = TraceStats()
    in_asm_pcs: set[int] = set()

    # ---- pass 1: in_asm (PC -> mnemonic), building the PC universe --------
    reader1 = _LimitedLineReader(log_path, max_bytes)
    for line in reader1:
        if not line or _NOISE_LINE_RE.match(line):
            continue
        m = _IN_ASM_RE.match(line)
        if m:
            pc = int(m.group(1), 16)
            mnemonic = m.group(3).lower()
            if pc not in stats.pc_mnemonic:      # first translation wins
                stats.pc_mnemonic[pc] = mnemonic
                stats.pc_text[pc] = f"{mnemonic} {m.group(4)}".rstrip()
            in_asm_pcs.add(pc)
        elif not _EXEC_RE.match(line):
            stats.unexpected_lines += 1
    truncated = reader1.truncated

    # ---- pass 2: exec lines; auto-locate the PC field ---------------------
    pc_index: int | None = None
    reader2 = _LimitedLineReader(log_path, max_bytes)
    for line in reader2:
        m = _EXEC_RE.match(line)
        if not m:
            continue
        fields = [int(x, 16) for x in m.group(1).split("/")]
        if pc_index is None:
            pc_index = _locate_pc_field(fields, in_asm_pcs)
            stats.pc_index_used = pc_index
        if pc_index is not None and pc_index < len(fields):
            pc = fields[pc_index]
            stats.exec_counts[pc] = stats.exec_counts.get(pc, 0) + 1
        else:
            stats.unexpected_lines += 1
    stats.truncated = truncated or reader2.truncated
    return stats


def _locate_pc_field(fields: list[int], in_asm_pcs: set[int]) -> int | None:
    """Find which bracket field is the guest PC.

    Preferred: the unique field whose value is a known IN: PC (executed PCs
    must all have been translated). Fallback: documented layout table.
    """
    if in_asm_pcs:
        hits = [i for i, v in enumerate(fields) if v in in_asm_pcs]
        if len(hits) == 1:
            return hits[0]
    return _FALLBACK_PC_INDEX.get(len(fields))
