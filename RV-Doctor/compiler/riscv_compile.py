"""FR2：RISC-V 交叉编译封装（xPack riscv-none-elf-gcc）。

调用纪律（02 文档 FR2 / 05 文档 P1）：
- 一律 subprocess 参数列表调用，禁止 shell=True；
- 无论成败，完整命令与输出都落盘 workdir/build.log（证据留存）；
- 失败以结构化 CompileResult 返回，不向编排层抛业务异常（环境缺工具除外）；
- 编译参数由 ARCH_MAP 统一管理，链接统一使用自带 rvcrt0.S + link.ld
  （04 文档 5.2 节"自带完整 link.ld"分支）与半主机运行时 specs（实测探测
  rdimon → semihost，配套 -mcmodel=medany；实测结论与依据见各常量注释）。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from compiler.config import ToolchainError, load_config, resolve_tool

# 合法架构 → march / mabi / 对应 QEMU 工具键（03 文档 5.1 节映射表）。
ARCH_MAP: dict[str, dict[str, str]] = {
    "rv32i": {"march": "rv32i", "mabi": "ilp32", "qemu_tool": "qemu_riscv32"},
    "rv64i": {"march": "rv64i", "mabi": "lp64", "qemu_tool": "qemu_riscv64"},
    "rv64gc": {"march": "rv64gc", "mabi": "lp64d", "qemu_tool": "qemu_riscv64"},
}
OPT_LEVELS: tuple[str, ...] = ("O0", "O1", "O2", "O3", "Os")

SPECS_CANDIDATES = ("rdimon.specs", "semihost.specs")  # 探测顺序：旧名优先
DEFAULT_SPECS = "rdimon.specs"  # build_gcc_command 的 specs 缺省值（裸文件名，探测结果同格式）
MC_MODEL = "-mcmodel=medany"  # RV64 默认 medlow 的 LUI 绝对寻址够不到 0x80000000（HI20 截断），
                              # 必须 PC 相对寻址；实测依据见 build.log 与 tests 编译矩阵
SMALL_DATA_LIMIT = "-msmall-data-limit=0"  # 关闭 gp 相对小数据，避免大全局数组撑破 gp ±2KiB 窗口
NOSTARTFILES = "-nostartfiles"  # 不链 newlib crt0（它假定固件已设好 sp，-bios none 下必死，见 rvcrt0.S）
NO_RWX_WARN = "-Wl,--no-warn-rwx-segments"  # 裸机单一段常驻 RAM，RWX 属预期，压掉告警噪声

# 自带最小运行时与链接脚本（04 文档 5.2 节"自带完整 link.ld"分支，M1 实测选定）：
# -Ttext=0x80000000 无法让全部段进 DRAM，且 -bios none 下无人初始化 sp。
RT0_PATH = Path(__file__).resolve().with_name("rvcrt0.S")
LD_SCRIPT = Path(__file__).resolve().with_name("link.ld")

LOG_NAME = "build.log"
ELF_NAME = "app.elf"

_specs_probe_cache: dict[str, str] = {}


def resolve_semihost_specs(gcc: Path) -> str:
    """探测工具链实际提供的半主机运行时 specs 文件名并缓存。

    `gcc -print-file-name=NAME` 找到时回显路径、找不到时原样回显 NAME，
    以此判定。rdimon 缺失（GCC 15.2+ 的 xPack 发行版）时退回 semihost。
    """
    key = str(gcc)
    cached = _specs_probe_cache.get(key)
    if cached:
        return cached
    for name in SPECS_CANDIDATES:
        try:
            proc = subprocess.run(
                [str(gcc), f"-print-file-name={name}"],
                capture_output=True, timeout=30.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolchainError(f"无法用 {gcc} 探测 specs 文件: {exc}") from exc
        answer = proc.stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0 and answer and answer != name:
            _specs_probe_cache[key] = name
            return name
    raise ToolchainError(
        "工具链未提供 rdimon.specs / semihost.specs（半主机运行时缺失），"
        "请检查 tools/gcc 安装完整性（docs/04-环境搭建指南.md FAQ）"
    )


@dataclass
class CompileResult:
    ok: bool
    elf: Path | None
    returncode: int  # gcc 退出码；超时强杀时为 -1
    log_path: Path  # build.log 全文留存
    stderr_tail: str  # 错误摘要（前端展示用）


def build_gcc_command(
    gcc_exe: Path, src: Path, arch: str, opt: str, workdir: Path,
    specs: str = DEFAULT_SPECS,
) -> tuple[list[str], Path]:
    """构造 gcc 参数列表并返回 (cmd, elf 路径)。arch/opt 非法时抛 ValueError。

    单独拆出：不运行进程即可测试参数映射是否符合 02 文档 FR2 的三架构五等级。
    specs 为半主机运行时 specs 文件名（由 resolve_semihost_specs 探测）。
    """
    if arch not in ARCH_MAP:
        raise ValueError(f"非法架构 {arch!r}，可选：{', '.join(ARCH_MAP)}")
    if opt not in OPT_LEVELS:
        raise ValueError(f"非法优化等级 {opt!r}，可选：{', '.join(OPT_LEVELS)}")
    target = ARCH_MAP[arch]
    elf = Path(workdir) / ELF_NAME
    cmd = [
        str(gcc_exe),
        f"-march={target['march']}",
        f"-mabi={target['mabi']}",
        f"-{opt}",
        MC_MODEL,
        SMALL_DATA_LIMIT,
        NOSTARTFILES,
        f"--specs={specs}",
        NO_RWX_WARN,
        f"-T{LD_SCRIPT}",
        str(RT0_PATH),
        str(src),
        "-o",
        str(elf),
    ]
    return cmd, elf


def compile_source(
    src: Path,
    arch: str,  # "rv32i" | "rv64i" | "rv64gc"
    opt: str,  # "O0" | "O1" | "O2" | "O3" | "Os"
    workdir: Path,
    timeout: float = 60.0,
) -> CompileResult:
    """把 C 源码编译为裸机 ELF（rdimon 半主机运行时）。

    成功与失败都写 workdir/build.log；超时按失败返回（returncode=-1）并在
    日志注明；gcc 缺失或无法启动时抛 ToolchainError；src 不存在时抛
    FileNotFoundError（调用方应在更早环节拦截输入问题）。
    """
    if arch not in ARCH_MAP:
        raise ValueError(f"非法架构 {arch!r}，可选：{', '.join(ARCH_MAP)}")
    if opt not in OPT_LEVELS:
        raise ValueError(f"非法优化等级 {opt!r}，可选：{', '.join(OPT_LEVELS)}")
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"源码不存在: {src}")

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    log_path = workdir / LOG_NAME

    gcc = resolve_tool("gcc", load_config())
    specs = resolve_semihost_specs(gcc)
    cmd, elf = build_gcc_command(gcc, src, arch, opt, workdir, specs=specs)

    def write_log(headline: str, stdout: bytes | None, stderr: bytes | None) -> None:
        body = "\n".join(
            [
                headline,
                "$ " + " ".join(cmd),
                "--- stdout ---",
                _decode(stdout),
                "--- stderr ---",
                _decode(stderr),
            ]
        )
        log_path.write_text(body, encoding="utf-8")

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        write_log(f"[compile] TIMEOUT 超过 {timeout} s，进程已终止", exc.stdout, exc.stderr)
        return CompileResult(
            ok=False,
            elf=None,
            returncode=-1,
            log_path=log_path,
            stderr_tail=f"编译超时（>{timeout:g}s），已终止；详见 {log_path}",
        )
    except OSError as exc:
        raise ToolchainError(f"无法启动 {gcc}: {exc}") from exc

    write_log(f"[compile] returncode={proc.returncode}", proc.stdout, proc.stderr)
    stderr_tail = _decode(proc.stderr)[-1500:]
    if proc.returncode != 0:
        return CompileResult(False, None, proc.returncode, log_path, stderr_tail)
    if not elf.is_file():
        return CompileResult(
            False, None, proc.returncode, log_path,
            f"gcc 返回 0 但未生成 {ELF_NAME}（异常，见 {log_path}）",
        )
    return CompileResult(True, elf, 0, log_path, stderr_tail)


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")
