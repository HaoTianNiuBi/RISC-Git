"""compiler/config.py 单元测试。"""
from __future__ import annotations

import pytest

from compiler.config import (
    DEFAULT_CONFIG,
    REPO_ROOT,
    ToolchainError,
    load_config,
    resolve_tool,
)
from tests.toolchain import requires_gcc


def test_load_config_has_all_sections():
    cfg = load_config()
    assert {"tools", "timeouts", "energy"} <= set(cfg)
    assert {"gcc", "objdump", "qemu_riscv64", "qemu_riscv32"} <= set(cfg["tools"])
    assert cfg["timeouts"]["compile"] > 0
    assert cfg["timeouts"]["run"] > 0


def test_load_config_energy_weights_match_design_doc():
    # 03 文档 5.6 节 DEFAULT_WEIGHTS：访存/乘除/浮点权重必须高于整数指令
    w = load_config()["energy"]["weights"]
    assert w["memory"] > w["integer"]
    assert w["muldiv"] > w["integer"]
    assert w["float"] > w["integer"]


def test_load_config_falls_back_when_toml_missing(tmp_path, monkeypatch):
    import compiler.config as cc

    monkeypatch.setattr(cc, "CONFIG_PATH", tmp_path / "no_such_config.toml")
    cfg = cc.load_config()
    assert cfg["tools"]["qemu_riscv64"] == DEFAULT_CONFIG["tools"]["qemu_riscv64"]


def test_load_config_deep_merges_nested_energy_weights(tmp_path, monkeypatch):
    import compiler.config as cc

    p = tmp_path / "config.toml"
    p.write_text("[energy.weights]\nmemory = 9.0\n", encoding="utf-8")
    monkeypatch.setattr(cc, "CONFIG_PATH", p)
    cfg = cc.load_config()
    assert cfg["energy"]["weights"]["memory"] == 9.0
    assert cfg["energy"]["weights"]["integer"] == DEFAULT_CONFIG["energy"]["weights"]["integer"]


def test_resolve_tool_missing_raises_with_install_hint(tmp_path):
    cfg = load_config()
    cfg["tools"]["gcc"] = str(tmp_path / "no_such_gcc.exe")
    with pytest.raises(ToolchainError, match="04-环境搭建指南"):
        resolve_tool("gcc", cfg)


def test_resolve_tool_unset_name_raises():
    cfg = load_config()
    cfg["tools"].pop("gcc", None)
    with pytest.raises(ToolchainError, match="未配置"):
        resolve_tool("gcc", cfg)


def test_resolve_tool_relative_paths_anchor_at_repo_root():
    # 相对路径必须相对仓库根解析，而不是相对进程 CWD
    cfg = load_config()
    cfg["tools"]["gcc"] = "definitely/not/here/gcc.exe"
    with pytest.raises(ToolchainError) as ei:
        resolve_tool("gcc", cfg)
    assert str(REPO_ROOT) in str(ei.value)


@requires_gcc
def test_resolve_tool_real_paths_exist():
    for name in ("gcc", "objdump", "qemu_riscv64", "qemu_riscv32"):
        assert resolve_tool(name).is_file(), f"{name} 路径配置指向不存在的文件"
