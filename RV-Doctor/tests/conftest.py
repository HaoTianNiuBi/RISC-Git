"""pytest 全局夹具：把仓库根目录加入 sys.path，并提供共享的样例源码。

工具链可用性标记在 tests/toolchain.py 中定义。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from tests.toolchain import CALLS_C, HELLO_C


@pytest.fixture
def hello_src(tmp_path: Path) -> Path:
    """样例程序 1：循环累加 + printf（对应 04 文档 hello.c）。"""
    p = tmp_path / "hello.c"
    p.write_text(HELLO_C, encoding="utf-8")
    return p


@pytest.fixture
def calls_src(tmp_path: Path) -> Path:
    """样例程序 2：循环内函数调用与乘法，覆盖 jal/mul 等指令。"""
    p = tmp_path / "calls.c"
    p.write_text(CALLS_C, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# profiler（M2）共享夹具：手工编码的冒烟镜像与真实 QEMU 11.1.0 trace 日志
# （生成脚本见 scripts/gen_smoke_elf.py，格式oracle见 tests/test_trace_parser.py）
FIXTURES = REPO_ROOT / "tests" / "fixtures"
TRACE_64 = FIXTURES / "smoke64_trace.log"
TRACE_32 = FIXTURES / "smoke32_trace.log"
TRACE_GCC_ANNOTATED = FIXTURES / "trace_gcc_annotated.log"


def qemu_exe() -> Path | None:
    """仓库便携版 QEMU 路径；未安装时返回 None（依赖它的用例自动 skip）。"""
    exe = REPO_ROOT / "tools" / "qemu" / "qemu-system-riscv64.exe"
    return exe if exe.exists() else None


requires_qemu = pytest.mark.skipif(
    qemu_exe() is None, reason="QEMU 不可用（tools/qemu 未就绪）"
)


def _copy_fixture(name: str, workdir: Path) -> Path:
    """镜像一律放进 ASCII 的 workdir：QEMU -kernel/-D 都要按路径打开文件。"""
    dst = workdir / name
    shutil.copy(FIXTURES / name, dst)
    return dst


@pytest.fixture
def workdir(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """独立任务工作目录，布局对齐 pipeline 的 workdir/<task_id>。

    必须放在仓库内（ASCII 路径）：QEMU for Windows 无法在含非 ASCII 字符的
    路径下打开 -D 日志文件（docs/04 FAQ 中文路径问题，pytest 系统临时目录
    位于 C:\\Users\\<用户名>\\ 下会踩中）。
    """
    d = REPO_ROOT / "workdir" / "_pytest" / f"{request.node.name[:60]}-{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def smoke64(workdir: Path) -> Path:
    """64 位冒烟镜像：10 次循环 + 半主机输出/退出（手工编码，无 GCC 依赖）。"""
    return _copy_fixture("smoke64.bin", workdir)


@pytest.fixture
def smoke32(workdir: Path) -> Path:
    """32 位冒烟镜像：同 smoke64 的 RV32 版本。"""
    return _copy_fixture("smoke32.bin", workdir)


@pytest.fixture
def hang64(workdir: Path) -> Path:
    """纯自跳转镜像（jal x0,0）：永不停机，专用于超时终止测试。"""
    return _copy_fixture("hang64.bin", workdir)
