"""QEMU bare-metal runner (docs/03 §5.3, FR3).

Runs an ELF (or raw image) on `qemu-system-riscv{32,64} -M virt` with newlib
semihosting, optionally capturing the exact-count trace log:

    -accel tcg,one-insn-per-tb=on -d in_asm,exec,nochain -D trace.log

The per-insn flag is chosen from the detected QEMU version (docs/04 §4):
QEMU >= 10  -> `-accel tcg,one-insn-per-tb=on`
QEMU 8.1~9.x -> `-one-insn-per-tb`
older        -> `-singlestep`

All external invocation uses argument lists (never shell=True) with a hard
timeout; on timeout the QEMU process is killed and the partial stdout is kept
as evidence.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

#: byte cap for captured program stdout (docs/03 §9 输出爆炸 对策)
MAX_STDOUT_BYTES = 1 << 20


@dataclass
class RunResult:
    ok: bool                 # 进程正常退出（stdout 可以为空）
    stdout: str
    exit_code: int | None
    timed_out: bool
    trace_path: Path | None
    # -- additive fields beyond the documented schema (keep defaults) --------
    stderr_tail: str = ""
    stdout_truncated: bool = False
    wall_time_s: float | None = None   # 参考值：受宿主负载影响，仅相对比较


class RunnerError(RuntimeError):
    """Raised when the QEMU binary cannot be located or version probed."""


def resolve_qemu(bitness: int, explicit: Path | str | None = None) -> Path:
    """Resolve the qemu-system-riscv{bits} executable.

    Priority: explicit argument > compiler.config (config.toml [tools] with
    the docs/04 portable-layout fallback). Raises RunnerError when missing.
    """
    if explicit is not None:
        exe = Path(explicit)
        if exe.is_dir():
            exe = exe / f"qemu-system-riscv{bitness or 64}.exe"
        if not exe.is_file():
            raise RunnerError(f"QEMU executable not found: {exe}")
        return exe
    try:
        from compiler.config import ToolchainError, load_config, resolve_tool

        return resolve_tool(f"qemu_riscv{bitness or 64}", load_config())
    except ToolchainError as exc:
        raise RunnerError(str(exc)) from exc


def elf_bitness(image: Path) -> int:
    """Return 32 or 64 from the ELF header class; 0 for non-ELF (raw image)."""
    with open(image, "rb") as f:
        magic = f.read(5)
    if len(magic) < 5 or magic[:4] != b"\x7fELF":
        return 0
    return 64 if magic[4] == 2 else 32


_version_cache: dict[Path, str] = {}


def qemu_version(qemu_exe: Path) -> str:
    """`qemu-system-*.exe --version` first line, e.g. 'QEMU emulator version 11.1.0 (...)'."""
    if qemu_exe in _version_cache:
        return _version_cache[qemu_exe]
    proc = subprocess.run(
        [str(qemu_exe), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30.0,
    )
    first = proc.stdout.splitlines()[0].strip() if proc.stdout else ""
    if proc.returncode != 0 or not first:
        raise RunnerError(f"cannot probe {qemu_exe.name}: rc={proc.returncode} {proc.stderr[:200]}")
    _version_cache[qemu_exe] = first
    return first


def choose_one_insn_flags(version_line: str) -> list[str]:
    """Per-insn-execution flags for the given QEMU version line (docs/04 §4).

    Raises RunnerError on unparseable version strings rather than guessing.
    """
    m = re.search(r"version\s+(\d+)\.(\d+)(?:\.(\d+))?", version_line)
    if not m:
        raise RunnerError(f"cannot parse QEMU version from: {version_line!r}")
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if (major, minor, patch) >= (10, 0, 0):
        return ["-accel", "tcg,one-insn-per-tb=on"]
    if (major, minor, patch) >= (8, 1, 0):
        return ["-one-insn-per-tb"]
    return ["-singlestep"]


def _build_args(
    qemu_exe: Path,
    image: Path,
    trace_path: Path | None,
    insn_flags: list[str],
    semihost_out: Path | None,
) -> list[str]:
    args = [
        str(qemu_exe),
        "-M", "virt",
        "-nographic",
        "-monitor", "none",   # keep program output clean (no monitor mux)
        "-serial", "none",    # UART unused (programs print via semihosting);
                              # avoids the stdio-chardev shutdown hang when
                              # stdin/stdout are subprocess pipes (race-y)
        "-bios", "none",
    ]
    if semihost_out is not None:
        # 半主机输出路由（QEMU 11.1.0/Windows 实测）：SYS_WRITE0 不走进程
        # stdout 而落在 stderr，与 QEMU 自身诊断混流；定向到文件 chardev 后
        # 单独落盘。newlib printf 的 SYS_WRITE(fd=1) 仍走进程 stdout，
        # 由 run_baremetal 双通道捕获拼接。
        args += [
            "-chardev", f"file,id=shcon,path={semihost_out}",
            "-semihosting-config", "enable=on,target=native,chardev=shcon",
        ]
    else:
        args += ["-semihosting-config", "enable=on,target=native"]
    args += list(insn_flags)
    if trace_path is not None:
        args += ["-d", "in_asm,exec,nochain", "-D", str(trace_path)]
    args += ["-kernel", str(image)]
    return args


def run_baremetal(
    image: Path,
    workdir: Path,
    timeout: float = 120.0,
    capture_trace: bool = True,
    one_insn_per_tb: bool = True,
    qemu_path: Path | None = None,
    bitness: int | None = None,
) -> RunResult:
    """Run `image` bare-metal under QEMU with semihosting; capture stdout + trace.

    `image` is a compiled ELF (preferred, from compiler.riscv_compile) or a raw
    binary linked at 0x80000000. Bitness comes from the ELF header when
    present; raw images MUST pass `bitness` (32 or 64) explicitly — guessing
    would run e.g. RV32 code on an RV64 CPU, where the 64-bit semihosting
    calling convention misreads SYS_EXIT's argument and the guest never exits.

    All artifacts land in `workdir`: stdout.txt, qemu_stderr.log, trace.log.
    All paths are resolved to absolute before spawning (QEMU's cwd differs).
    """
    image = Path(image).resolve()
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if not image.exists():
        raise RunnerError(f"image not found: {image}")

    detected = elf_bitness(image)
    if bitness is None:
        bitness = detected
    if bitness not in (32, 64):
        raise RunnerError(
            f"cannot determine bitness for {image.name} (raw image): "
            "pass bitness=32 or bitness=64 explicitly"
        )

    qemu_exe = resolve_qemu(bitness, qemu_path)

    insn_flags = choose_one_insn_flags(qemu_version(qemu_exe)) if one_insn_per_tb else []
    trace_path = (workdir / "trace.log") if capture_trace else None
    semihost_out = workdir / "semihost_out.txt"
    args = _build_args(qemu_exe, image, trace_path, insn_flags, semihost_out)

    started = time.perf_counter()
    timed_out = False
    stdout_bytes = b""
    stderr_bytes = b""
    returncode: int | None = None
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=str(workdir),
        )
        stdout_bytes, stderr_bytes = proc.stdout, proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout_bytes = exc.stdout or b""
        stderr_bytes = exc.stderr or b""
    wall = time.perf_counter() - started

    stdout_truncated = False
    if len(stdout_bytes) > MAX_STDOUT_BYTES:
        stdout_bytes = stdout_bytes[:MAX_STDOUT_BYTES]
        stdout_truncated = True
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")

    # 程序输出有两条互不相通的半主机通路（QEMU 11.1.0/Windows 实测）：
    #   SYS_WRITE0        -> 半主机 chardev（semihost_out.txt）
    #   SYS_WRITE(fd=1)   -> QEMU 进程 stdout（newlib printf 走这条，不进 chardev）
    # 程序输出 = 两者拼接（chardev 部分在前）；证据链另存 qemu_stdout.log。
    semihost_part = ""
    if semihost_out.exists():
        semihost_part = semihost_out.read_text(encoding="utf-8", errors="replace")
    program_out = semihost_part + stdout_text
    if len(program_out) > MAX_STDOUT_BYTES:
        program_out = program_out[:MAX_STDOUT_BYTES]
        stdout_truncated = True
    (workdir / "stdout.txt").write_text(program_out, encoding="utf-8")
    (workdir / "qemu_stdout.log").write_text(stdout_text, encoding="utf-8")
    (workdir / "qemu_stderr.log").write_text(stderr_text, encoding="utf-8")

    # 合法的裸机程序可以只返回状态码而不输出任何内容；空 stdout 不能
    # 被当成运行失败。输出是否为空由调用方按具体验证场景决定。
    ok = (not timed_out) and returncode == 0
    return RunResult(
        ok=ok,
        stdout=program_out,
        exit_code=returncode,
        timed_out=timed_out,
        trace_path=trace_path if (trace_path is not None and trace_path.exists()) else None,
        stderr_tail=stderr_text[-2000:],
        stdout_truncated=stdout_truncated,
        wall_time_s=wall,
    )
