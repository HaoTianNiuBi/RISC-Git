#!/usr/bin/env python3
"""Generate minimal bare-metal RISC-V smoke-test ELFs without a toolchain.

Hand-encodes a handful of RV32/RV64 machine instructions (Python structural
encoding, no assembler needed) into a single PT_LOAD ELF loaded by
`qemu-system-riscv{32,64} -M virt -bios none -kernel`.

Program behaviour (fixed, hand-verifiable):
  1. semihosting SYS_WRITE0  -> prints "rv-doctor smoke: loop done"
  2. a 10-iteration loop: addi t0,t0,1 ; blt t0,t1,loop  (each executes 10x)
  3. semihosting SYS_EXIT(ADP_Stopped_ApplicationExit) -> QEMU exits 0

Purpose: lock the real QEMU 11.1.0 trace log format (risk T1) and provide a
deterministic dynamic-count oracle before/without the cross GCC.
"""
from __future__ import annotations

import struct
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "workdir" / "smoke_images"

# ---------------------------------------------------------------- encoders --
def i_type(imm: int, rs1: int, funct3: int, rd: int, opcode: int = 0x13) -> int:
    return ((imm & 0xFFF) << 20) | ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | ((rd & 0x1F) << 7) | opcode


def b_type(imm: int, rs1: int, rs2: int, funct3: int, opcode: int = 0x63) -> int:
    imm &= 0x1FFF
    return (
        (((imm >> 12) & 1) << 31)
        | (((imm >> 5) & 0x3F) << 25)
        | ((rs2 & 0x1F) << 20)
        | ((rs1 & 0x1F) << 15)
        | ((funct3 & 0x7) << 12)
        | (((imm >> 1) & 0xF) << 8)
        | (((imm >> 11) & 1) << 7)
        | opcode
    )


def u_type(imm20: int, rd: int, opcode: int) -> int:
    return ((imm20 & 0xFFFFF) << 12) | ((rd & 0x1F) << 7) | opcode


def addi(rd: int, rs1: int, imm: int) -> int:
    return i_type(imm, rs1, 0, rd, 0x13)


def li(rd: int, imm: int) -> list[int]:  # any 32-bit immediate (lui+addi when large)
    if -2048 <= imm <= 2047:
        return [addi(rd, 0, imm)]
    hi20 = (imm + 0x800) >> 12
    lo = imm - (hi20 << 12)
    return [u_type(hi20, rd, 0x37), addi(rd, rd, lo)]


def la(rd: int, pc: int, addr: int) -> tuple[int, int]:
    """auipc/addi pair; %hi/%lo arithmetic relative to the auipc's own PC."""
    delta = addr - pc
    hi = (delta + 0x800) >> 12
    lo = delta - (hi << 12)
    return u_type(hi, rd, 0x17), addi(rd, rd, lo)


# standard RISC-V semihosting entry sequence (QEMU: riscv_semihosting())
SLLI_ZZ_1F = i_type(0x1F, 0, 1, 0)  # slli zero,zero,0x1f = 0x01F01013
EBREAK = 0x00100073
SRAI_ZZ_7 = 0x40705013              # srai zero,zero,7
HANG = 0x0000006F                   # jal x0,0 -> jump to self (never reached)

# registers (ABI numbers)
ZERO, RA, A0, A1, T0, T1 = 0, 1, 10, 11, 5, 6
SYS_WRITE0, SYS_EXIT = 0x04, 0x18
ADP_STOPPED_APPLICATION_EXIT = 0x20026

MSG = b"rv-doctor smoke: loop done\n"


def _assemble(base: int, exit_mode: str) -> tuple[bytes, int]:
    """Assemble the fixed program. exit_mode: 'block64' (a1=ptr to {reason,subcode})
    or 'imm32' (a1=reason). Returns (code_bytes, msg_addr)."""
    words: list[int] = []
    pc = base
    # la a1, msg  -- patched after layout is known
    la1_at = pc
    words += [0, 0]; pc += 8
    words += li(A0, SYS_WRITE0); pc += 4
    words += [SLLI_ZZ_1F, EBREAK, SRAI_ZZ_7]; pc += 12
    words += li(T0, 0); pc += 4
    words += li(T1, 10); pc += 4
    loop_pc = pc
    words.append(addi(T0, T0, 1)); pc += 4      # loop: executed 10x
    words.append(b_type(loop_pc - pc, T0, T1, 4)); pc += 4  # blt t0,t1,loop (10x)
    if exit_mode == "block64":
        words += [0, 0]; pc += 8                # la a1, exit_block
        words += li(A0, SYS_EXIT); pc += 4
    else:  # imm32: reason directly in a1
        words += li(A1, ADP_STOPPED_APPLICATION_EXIT); pc += 4
        words += li(A0, SYS_EXIT); pc += 4
    words += [SLLI_ZZ_1F, EBREAK, SRAI_ZZ_7]; pc += 12
    words.append(HANG); pc += 4

    n_insns = len(words)
    msg_addr = base + n_insns * 4
    pad = (-(len(MSG))) % 8                     # keep trailing data 8-byte aligned
    exit_addr = msg_addr + len(MSG) + pad

    a_hi, a_lo = la(A1, la1_at, msg_addr)
    words[0], words[1] = a_hi, a_lo
    if exit_mode == "block64":
        e_hi, e_lo = la(A1, base + 10 * 4, exit_addr)
        words[10], words[11] = e_hi, e_lo

    code = b"".join(struct.pack("<I", w) for w in words)
    return code, msg_addr


def build_elf64(payload: bytes, entry: int) -> bytes:
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8  # 64-bit LE SYSV
    ehdr = ident + struct.pack(
        "<HHIQQQIHHHHHH", 2, 243, 1, entry, 64, 0, 0, 64, 56, 1, 0, 0, 0
    )
    phdr = struct.pack("<IIQQQQQQ", 1, 7, 0, 0x80000000, 0x80000000,
                       len(payload), len(payload), 0x1000)
    return ehdr + phdr + payload


def build_elf32(payload: bytes, entry: int) -> bytes:
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8  # 32-bit LE SYSV
    ehdr = ident + struct.pack(
        "<HHIIIIIHHHHHH", 2, 243, 1, entry, 52, 0, 0, 52, 32, 1, 0, 0, 0
    )
    phdr = struct.pack("<IIIIIIII", 1, 0, 0x80000000, 0x80000000,
                       len(payload), len(payload), 7, 0x1000)
    return ehdr + phdr + payload


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # ---- raw binaries: -kernel loads these at 0x80000000 = entry (no ELF parsing)
    for bits in (64, 32):
        mode = "block64" if bits == 64 else "imm32"
        base = 0x80000000
        code, msg = _assemble(base, exit_mode=mode)
        payload = code + MSG + b"\x00" * ((-(len(MSG))) % 8)
        if bits == 64:
            payload += struct.pack("<QQ", ADP_STOPPED_APPLICATION_EXIT, 0)
        else:
            payload += struct.pack("<II", ADP_STOPPED_APPLICATION_EXIT, 0)
        (OUTPUT / f"smoke{bits}.bin").write_bytes(payload)
        print(f"smoke{bits}.bin: {len(payload)} bytes, code@0x{base:08x}, msg@0x{msg:08x}")

    # ---- ELF wrappers (header bytes prepended; code base shifts accordingly)
    base64 = 0x80000000 + 120  # ELF64 ehdr(64) + phdr(56)
    code64, _ = _assemble(base64, exit_mode="block64")
    payload64 = code64 + MSG + b"\x00" * ((-(len(MSG))) % 8)
    payload64 += struct.pack("<QQ", ADP_STOPPED_APPLICATION_EXIT, 0)
    (OUTPUT / "smoke64.elf").write_bytes(build_elf64(payload64, entry=base64))
    print(f"smoke64.elf: {len(payload64) + 120} bytes, code@0x{base64:08x}")

    base32 = 0x80000000 + 72  # ELF32 ehdr(40) + phdr(32)
    code32, _ = _assemble(base32, exit_mode="imm32")
    payload32 = code32 + MSG + b"\x00" * ((-(len(MSG))) % 8)
    payload32 += struct.pack("<II", ADP_STOPPED_APPLICATION_EXIT, 0)
    (OUTPUT / "smoke32.elf").write_bytes(build_elf32(payload32, entry=base32))
    print(f"smoke32.elf: {len(payload32) + 72} bytes, code@0x{base32:08x}")

    # ---- hang image: pure `jal x0,0` self-loop, for runner timeout tests ----
    (OUTPUT / "hang64.bin").write_bytes(struct.pack("<I", HANG))
    print(f"hang64.bin: {4} bytes (self-loop; never exits; used for timeout test)")
    print(f"Output directory: {OUTPUT}")


if __name__ == "__main__":
    main()
