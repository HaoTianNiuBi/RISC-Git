"""compiler/riscv_compile.py 单元测试（05 文档 M1 要求 ≥5 个用例：
正常编译、语法错误、超时、非法 arch、objdump/工具缺失）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from compiler.config import ToolchainError
from compiler.riscv_compile import (
    ARCH_MAP,
    OPT_LEVELS,
    build_gcc_command,
    compile_source,
)
from tests.toolchain import requires_gcc


# ---------- 不依赖工具链 ----------


@pytest.mark.parametrize("bad", ["rv64", "x86", "", "RV64I", "rv64imc"])
def test_invalid_arch_raises(bad, tmp_path):
    with pytest.raises(ValueError, match="非法架构"):
        compile_source(tmp_path / "x.c", bad, "O2", tmp_path)


@pytest.mark.parametrize("bad", ["O4", "ofast", "o2", "", "O"])
def test_invalid_opt_raises(bad, tmp_path):
    with pytest.raises(ValueError, match="非法优化等级"):
        compile_source(tmp_path / "x.c", "rv64i", bad, tmp_path)


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        compile_source(tmp_path / "nope.c", "rv64i", "O0", tmp_path)


def test_build_command_maps_all_arch_and_opt_levels():
    for arch, target in ARCH_MAP.items():
        cmd, elf = build_gcc_command(
            Path("gcc.exe"), Path("a.c"), arch, "Os", Path("w"), specs="semihost.specs"
        )
        assert isinstance(cmd, list) and all(isinstance(a, str) for a in cmd)
        assert f"-march={target['march']}" in cmd
        assert f"-mabi={target['mabi']}" in cmd
        # M1 实测固化的链接参数：medany 代码模型 + 自带 crt0/link.ld（见模块注释）
        assert "-mcmodel=medany" in cmd
        assert "-msmall-data-limit=0" in cmd
        assert "-nostartfiles" in cmd
        assert "--specs=semihost.specs" in cmd
        assert any(a.startswith("-T") and a.endswith("link.ld") for a in cmd)
        assert any(a.endswith("rvcrt0.S") for a in cmd)
        assert cmd[-1] == str(Path("w") / "app.elf")
        # 参数列表在前，源文件在后、-o 输出收尾（02 文档 FR2 禁止 shell 拼接的前提）
        assert cmd.index("a.c") < cmd.index("-o")

    for opt in OPT_LEVELS:
        cmd, _ = build_gcc_command(Path("gcc.exe"), Path("a.c"), "rv64i", opt, Path("w"))
        assert f"-{opt}" in cmd
    # 默认 specs 值保持 rdimon（旧工具链兼容），由 resolve_semihost_specs 运行时探测覆盖
    cmd, _ = build_gcc_command(Path("gcc.exe"), Path("a.c"), "rv64i", "O0", Path("w"))
    assert "--specs=rdimon.specs" in cmd


def test_build_command_rejects_bad_values():
    with pytest.raises(ValueError):
        build_gcc_command(Path("g"), Path("a.c"), "rv32x", "O0", Path("w"))
    with pytest.raises(ValueError):
        build_gcc_command(Path("g"), Path("a.c"), "rv32i", "O9", Path("w"))


def test_missing_gcc_raises_toolchain_error(tmp_path, monkeypatch):
    import compiler.riscv_compile as rc

    cfg = rc.load_config()
    cfg["tools"]["gcc"] = str(tmp_path / "no_such_gcc.exe")
    monkeypatch.setattr(rc, "load_config", lambda: cfg)
    src = tmp_path / "a.c"
    src.write_text("int main(void){return 0;}\n", encoding="utf-8")
    with pytest.raises(ToolchainError, match="04-环境搭建指南"):
        rc.compile_source(src, "rv64i", "O0", tmp_path / "out")


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout = stdout
        self.stderr = b""
        self.returncode = returncode


def test_specs_probe_prefers_rdimon(tmp_path, monkeypatch):
    """旧工具链提供 rdimon.specs 时应直接选中。"""
    import compiler.riscv_compile as rc

    calls: list[str] = []

    def fake_run(cmd, capture_output, timeout):
        calls.append(cmd[1])
        name = cmd[1].split("=")[1]
        out = b"D:/tc/lib/rdimon.specs\n" if name == "rdimon.specs" else name.encode() + b"\n"
        return _FakeProc(out)

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    rc._specs_probe_cache.clear()
    assert rc.resolve_semihost_specs(tmp_path / "gcc.exe") == "rdimon.specs"
    assert calls == ["-print-file-name=rdimon.specs"]


def test_specs_probe_falls_back_to_semihost(tmp_path, monkeypatch):
    """xPack GCC 15.2 起 rdimon.specs 缺失，必须退回 semihost.specs。"""
    import compiler.riscv_compile as rc

    def fake_run(cmd, capture_output, timeout):
        # gcc -print-file-name 找不到时原样回显文件名；semihost 存在时回显路径
        name = cmd[1].split("=")[1]
        out = name.encode() if name == "rdimon.specs" else b"D:/tc/lib/semihost.specs\n"
        return _FakeProc(out + b"\n")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    rc._specs_probe_cache.clear()
    assert rc.resolve_semihost_specs(tmp_path / "gcc.exe") == "semihost.specs"


def test_specs_probe_none_available_raises(tmp_path, monkeypatch):
    import compiler.riscv_compile as rc

    def fake_run(cmd, capture_output, timeout):
        name = cmd[1].split("=")[1]
        return _FakeProc(name.encode() + b"\n")  # 全部原样回显 = 全部缺失

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    rc._specs_probe_cache.clear()
    with pytest.raises(ToolchainError, match="semihost"):
        rc.resolve_semihost_specs(tmp_path / "gcc.exe")


def test_specs_probe_result_is_cached(tmp_path, monkeypatch):
    import compiler.riscv_compile as rc

    n = {"count": 0}

    def fake_run(cmd, capture_output, timeout):
        n["count"] += 1
        return _FakeProc(b"D:/tc/lib/rdimon.specs\n")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    rc._specs_probe_cache.clear()
    rc.resolve_semihost_specs(tmp_path / "gcc.exe")
    rc.resolve_semihost_specs(tmp_path / "gcc.exe")
    assert n["count"] == 1  # 第二次命中缓存，不再起进程


# ---------- 依赖工具链（tools/gcc 未就绪时自动 skip） ----------


@requires_gcc
def test_compile_success_and_log(hello_src, tmp_path):
    result = compile_source(hello_src, "rv64gc", "O2", tmp_path / "out")
    assert result.ok, f"编译失败: {result.stderr_tail}"
    assert result.returncode == 0
    assert result.elf is not None and result.elf.is_file()
    assert result.elf == tmp_path / "out" / "app.elf"
    log = result.log_path.read_text(encoding="utf-8")
    assert "riscv-none-elf-gcc" in log
    assert "returncode=0" in log


@requires_gcc
def test_compile_syntax_error_structured(hello_src, tmp_path):
    bad = hello_src.with_name("bad.c")
    bad.write_text("int main(void) { return oops; }\n", encoding="utf-8")
    result = compile_source(bad, "rv64i", "O0", tmp_path / "out")
    assert not result.ok
    assert result.elf is None
    assert result.returncode != 0
    assert "error" in result.stderr_tail.lower()
    assert result.log_path.is_file()
    log = result.log_path.read_text(encoding="utf-8")
    assert f"returncode={result.returncode}" in log


@requires_gcc
def test_compile_timeout_returns_structured_failure(hello_src, tmp_path):
    # 任何真实编译（进程启动 + 链接）都不可能在 50ms 内完成，超时路径必然触发
    result = compile_source(hello_src, "rv64i", "O0", tmp_path / "out", timeout=0.05)
    assert not result.ok
    assert result.elf is None
    assert result.returncode == -1
    assert "超时" in result.stderr_tail
    log = result.log_path.read_text(encoding="utf-8")
    assert "TIMEOUT" in log


@requires_gcc
@pytest.mark.parametrize("which", ["hello", "calls"])
@pytest.mark.parametrize("arch", ["rv32i", "rv64i", "rv64gc"])
@pytest.mark.parametrize("opt", ["O0", "O1", "O2", "O3", "Os"])
def test_compile_matrix_2_programs_3_arch_5_opt(which, arch, opt, hello_src, calls_src, tmp_path):
    """M1 DoD：2 个样例程序在 3 架构 × 5 优化等级下全部编译出 ELF。"""
    src = hello_src if which == "hello" else calls_src
    result = compile_source(src, arch, opt, tmp_path / f"{which}-{arch}-{opt}")
    assert result.ok, f"{which} {arch} {opt} 编译失败: {result.stderr_tail}"
    assert result.elf is not None and result.elf.is_file()
