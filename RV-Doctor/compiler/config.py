"""配置加载与工具链路径解析（03 文档第 7 节）。

config.toml 位于仓库根目录；其中的相对路径一律以仓库根目录为基准解析。
config.toml 缺失或条目不全时使用本模块的兜底值。外部工具缺失时抛出
ToolchainError，提示信息指向 docs/04-环境搭建指南.md 的安装章节。
"""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.toml"

# 兜底配置；tools 路径与 04 文档的 tools\ 便携安装布局保持一致。
DEFAULT_CONFIG: dict = {
    "tools": {
        "gcc": "tools/gcc/bin/riscv-none-elf-gcc.exe",
        "objdump": "tools/gcc/bin/riscv-none-elf-objdump.exe",
        "qemu_riscv64": "tools/qemu/qemu-system-riscv64.exe",
        "qemu_riscv32": "tools/qemu/qemu-system-riscv32.exe",
    },
    "timeouts": {"compile": 60.0, "run": 120.0, "disasm": 60.0},
    "energy": {
        "weights": {
            "integer": 1.0,
            "memory": 3.0,
            "branch": 1.5,
            "jump": 1.0,
            "muldiv": 4.0,
            "float": 8.0,
            "system": 1.0,
        }
    },
}


class ToolchainError(RuntimeError):
    """外部工具（gcc / objdump / qemu）未配置、缺失或无法启动。"""


def load_config() -> dict:
    """读取 config.toml 并按节合并到兜底值之上；文件缺失时返回兜底值。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        with CONFIG_PATH.open("rb") as f:
            user = tomllib.load(f)
        def merge(dst: dict, src: dict) -> None:
            for key, value in src.items():
                if isinstance(value, dict) and isinstance(dst.get(key), dict):
                    merge(dst[key], value)
                else:
                    dst[key] = value

        merge(cfg, user)
    return cfg


def resolve_tool(name: str, config: dict | None = None) -> Path:
    """把 [tools] 中的条目解析为真实存在的可执行文件路径。"""
    cfg = config if config is not None else load_config()
    raw = cfg["tools"].get(name)
    if not raw:
        raise ToolchainError(f"config.toml [tools] 未配置工具 {name!r}")
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        raise ToolchainError(
            f"未找到 {name}: {path}\n"
            f"请按 docs/04-环境搭建指南.md 第 3/4 节安装工具链，"
            f"或在 config.toml [tools] 中修正 {name} 的路径。"
        )
    return path
