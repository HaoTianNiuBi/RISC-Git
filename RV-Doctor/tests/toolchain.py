"""工具链可用性探测与样例源码（供各测试模块共享）。

工具链探测在收集阶段执行一次：tools/ 尚未就绪时，依赖 gcc/objdump 的
用例自动 skip，不误报失败；工具链无关用例（参数构造、日志解析等）始终运行。
"""
from __future__ import annotations

import pytest

from compiler.config import ToolchainError, load_config, resolve_tool


def _tool_present(name: str) -> bool:
    try:
        resolve_tool(name, load_config())
    except (ToolchainError, KeyError):
        return False
    return True


requires_gcc = pytest.mark.skipif(
    not _tool_present("gcc"),
    reason="riscv-none-elf-gcc 不可用（tools/gcc 未就绪或 config.toml 未配置）",
)
requires_objdump = pytest.mark.skipif(
    not _tool_present("objdump"),
    reason="riscv-none-elf-objdump 不可用（tools/gcc 未就绪或 config.toml 未配置）",
)
requires_qemu = pytest.mark.skipif(
    not (_tool_present("qemu_riscv64") and _tool_present("qemu_riscv32")),
    reason="qemu-system-riscv{32,64} 不可用（tools/qemu 未就绪或 config.toml 未配置）",
)

# 样例程序 1：与 04 文档 5.1 节 hello.c 一致（预期输出 sum=5050）。
HELLO_C = """\
#include <stdio.h>

int main(void) {
    int sum = 0;
    for (int i = 1; i <= 100; i++) {
        sum += i;
    }
    printf("sum=%d\\n", sum);
    return 0;
}
"""

# 样例程序 2：循环内函数调用 + 乘法（预期输出 acc=2870）。
CALLS_C = """\
#include <stdio.h>

static int square(int x) {
    return x * x;
}

int main(void) {
    int acc = 0;
    for (int i = 1; i <= 20; i++) {
        acc += square(i);
    }
    printf("acc=%d\\n", acc);
    return 0;
}
"""
