"""runner 单元测试（无工具链依赖：版本→参数映射、ELF 位宽探测、参数构造）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from profiler.runner import RunnerError, _build_args, choose_one_insn_flags, elf_bitness, run_baremetal


# ---- 版本 → 逐条执行参数（docs/04 §4 的实测版本分界）--------------------------
def test_flags_qemu11_uses_accel_property():
    assert choose_one_insn_flags(
        "QEMU emulator version 11.1.0 (v11.1.0-12130-ge470268ff4)"
    ) == ["-accel", "tcg,one-insn-per-tb=on"]


def test_flags_qemu9_and_81_use_standalone_flag():
    assert choose_one_insn_flags("QEMU emulator version 9.0.2") == ["-one-insn-per-tb"]
    assert choose_one_insn_flags("QEMU emulator version 8.1.0") == ["-one-insn-per-tb"]


def test_flags_older_versions_use_singlestep():
    assert choose_one_insn_flags("QEMU emulator version 8.0.0") == ["-singlestep"]
    assert choose_one_insn_flags("QEMU emulator version 7.2.14") == ["-singlestep"]


def test_flags_unparseable_version_raises():
    with pytest.raises(RunnerError):
        choose_one_insn_flags("QEMU emulator version unknown")


# ---- ELF 位宽探测 --------------------------------------------------------------
def test_elf_bitness_detection(tmp_path):
    e64 = tmp_path / "a.elf"
    e64.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 9)
    e32 = tmp_path / "b.elf"
    e32.write_bytes(b"\x7fELF\x01\x01\x01" + b"\x00" * 9)
    raw = tmp_path / "c.bin"
    raw.write_bytes(b"\x13\x00\x00\x00")

    assert elf_bitness(e64) == 64
    assert elf_bitness(e32) == 32
    assert elf_bitness(raw) == 0  # 非 ELF（原始镜像）


def test_raw_image_requires_explicit_bitness(workdir):
    """裸镜像不猜位宽：猜错会把 RV32 代码跑在 RV64 CPU 上（SYS_EXIT 参数
    被按 64 位指针解引用，guest 永不退出）。必须显式声明。"""
    raw = workdir / "raw.bin"
    raw.write_bytes(b"\x13\x00\x00\x00")
    with pytest.raises(RunnerError, match="bitness"):
        run_baremetal(raw, workdir)


def test_empty_stdout_is_still_success(workdir, smoke64):
    # smoke64 prints output in the fixture; this test only locks the runner's
    # success contract through a minimal mocked subprocess result.
    import profiler.runner as rr
    class P:
        stdout = b""
        stderr = b""
        returncode = 0
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rr.subprocess, "run", lambda *a, **k: P())
        mp.setattr(rr, "qemu_version", lambda p: "QEMU emulator version 11.1.0")
        mp.setattr(rr, "resolve_qemu", lambda bits, explicit=None: Path("qemu.exe"))
        mp.setattr(rr, "elf_bitness", lambda p: 64)
        result = rr.run_baremetal(smoke64, workdir, capture_trace=False)
    assert result.ok
    assert result.stdout == ""


# ---- QEMU 参数构造（subprocess 用参数列表，禁 shell=True）------------------------
def test_build_args_trace_and_kernel():
    args = _build_args(
        "qemu-system-riscv64.exe",
        "app.elf",
        "trace.log",
        ["-accel", "tcg,one-insn-per-tb=on"],
        "semihost_out.txt",
    )
    assert args[:10] == [
        "qemu-system-riscv64.exe", "-M", "virt", "-nographic",
        "-monitor", "none", "-serial", "none", "-bios", "none",
    ]
    # 半主机控制台定向到文件（Windows 版 QEMU 的 SYS_WRITE0 输出走 stderr，
    # 实测 11.1.0；文件 chardev 让程序输出独立落盘）
    i = args.index("-chardev")
    assert args[i + 1] == "file,id=shcon,path=semihost_out.txt"
    assert args[args.index("-semihosting-config") + 1] == "enable=on,target=native,chardev=shcon"
    assert args[args.index("-accel") + 1] == "tcg,one-insn-per-tb=on"
    assert args[args.index("-d") + 1] == "in_asm,exec,nochain"
    assert args[args.index("-D") + 1] == "trace.log"
    assert args[-2:] == ["-kernel", "app.elf"]
    assert all(isinstance(a, str) for a in args)  # 纯参数列表


def test_build_args_without_trace_has_no_d_flags():
    args = _build_args("qemu", "app.elf", None, [], None)
    assert "-d" not in args and "-D" not in args
    # 无 semihost_out 时回退为经典 semihosting 参数
    assert args[args.index("-semihosting-config") + 1] == "enable=on,target=native"
    assert "-chardev" not in args
    assert args[-2:] == ["-kernel", "app.elf"]
