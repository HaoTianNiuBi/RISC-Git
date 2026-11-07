#!/usr/bin/env python3
"""M3 基准冻结：跑 10 个基准 → 独立参考实现复算校验和 → 一致才冻结产物。

流程（docs/06 §4 "基准首次合入时人工确认输出正确并固化"的机械化版）：
1. 对每个基准用独立 Python 参考实现（同 LCG、同算术、不同语言）算出期望输出行；
2. pipeline 真实编译 + 运行（rv64gc/O0，含确定性校验）；
3. 实测 stdout 与参考实现逐行比对，不一致则拒绝冻结并退出非零；
4. 一致 → 写 benchmarks/expected/<name>.out 与 benchmarks/snapshots/<name>.json。

b03 的 CRC 用 zlib.crc32 作第三方裁判（比复写同一算法更强的独立性）。
"""
from __future__ import annotations

import json
import re
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import run_checkup  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BM_DIR = REPO / "benchmarks"
EXPECTED = BM_DIR / "expected"
SNAPSHOTS = BM_DIR / "snapshots"

MASK32 = 0xFFFFFFFF


def lcg(x: int) -> int:
    return (x * 1103515245 + 12345) & MASK32


# ---- 各基准的独立参考实现：返回期望 stdout（不含行尾换行差异）--------------

def ref_b01() -> str:
    x, s = 20260917, 0
    for _ in range(2000):
        x = lcg(x)
        s += (x >> 16) & 0xFFFF
    assert s < 2**31
    return f"b01 sum_array: N=2000 sum={s}"


def ref_b02() -> str:
    x = 1
    A = [[0] * 16 for _ in range(16)]
    B = [[0] * 16 for _ in range(16)]
    for i in range(16):
        for j in range(16):
            x = lcg(x)
            A[i][j] = (x >> 16) % 100
    for i in range(16):
        for j in range(16):
            x = lcg(x)
            B[i][j] = (x >> 16) % 100
    chk = 0
    for i in range(16):
        for j in range(16):
            chk += sum(A[i][k] * B[k][j] for k in range(16))
    return f"b02 matrix_mul: N=16 checksum={chk}"


def ref_b03() -> str:
    x = 424242
    buf = bytearray(96)
    for i in range(96):
        x = lcg(x)
        buf[i] = (x >> 24) & 0xFF
    crc = zlib.crc32(bytes(buf)) & MASK32  # 第三方裁判
    return f"b03 crc32: len=96 crc={crc:08x}"


def ref_b04() -> str:
    x = 777
    text = [""] * 1200
    for i in range(1200):
        x = lcg(x)
        text[i] = chr(ord("a") + (x >> 16) % 4)
    pat = [""] * 10
    for i in range(10):
        x = lcg(x)
        pat[i] = chr(ord("a") + (x >> 16) % 4)
    for off in (100, 500, 900):
        for i in range(10):
            text[off + i] = pat[i]
    t, p = "".join(text), "".join(pat)
    matches, last = 0, -1
    for i in range(len(t) - len(p) + 1):
        if t[i:i + 10] == p:
            matches += 1
            last = i
    return f"b04 string_search: T=1200 P=10 matches={matches} last={last}"


def ref_b05() -> str:
    x = 20260101
    v = [0] * 120
    for i in range(120):
        x = lcg(x)
        v[i] = (x >> 16) % 10000
    swaps = 0
    for i in range(119):
        for j in range(119 - i):
            if v[j] > v[j + 1]:
                v[j], v[j + 1] = v[j + 1], v[j]
                swaps += 1
    chk = sum(v[i] * (i % 7 + 1) for i in range(120))
    return f"b05 bubble_sort: N=120 swaps={swaps} checksum={chk}"


def ref_b06() -> str:
    x = 987654321
    acc = 0
    for _ in range(256):
        x = lcg(x)
        r, g, b = (x >> 24) & 0xFF, (x >> 16) & 0xFF, (x >> 8) & 0xFF
        # float32 语义：用 struct 模拟（各 ISA float32 结果一致，golden 稳定）
        import struct
        gray = struct.unpack("f", struct.pack("f", 0.299 * r + 0.587 * g + 0.114 * b))[0]
        acc += int(gray * 4.0)
    return f"b06 grayscale: N=256 acc={acc}"


def ref_b07() -> str:
    calls = 0

    def fib(n: int) -> int:
        nonlocal calls
        calls += 1
        if n < 2:
            return n
        return fib(n - 1) + fib(n - 2)

    r = fib(17)
    return f"b07 fib: n=17 fib={r} calls={calls}"


def ref_b08() -> str:
    x = 31415926
    a, b = [0] * 1500, [0] * 1500
    for i in range(1500):
        x = lcg(x)
        a[i] = (x >> 16) % 500
    for i in range(1500):
        x = lcg(x)
        b[i] = (x >> 16) % 500
    dot = sum(a[i] * b[i] for i in range(1500))
    assert dot < 2**31
    return f"b08 dot_product: N=1500 dot={dot}"


def ref_b09() -> str:
    x = 555
    s = [""] * 200
    for i in range(200):
        x = lcg(x)
        s[i] = chr(ord("a") + (x >> 16) % 26)
    for pos in (10, 30, 55, 80, 105, 130):
        s[pos] = "w"
    text = "".join(s)
    n = len(text)
    count = 0
    for i in range(150):
        if text[i % n] == "w":
            count += 1
    return f"b09 strlen_loop: LEN=200 ITER=150 count={count}"


def ref_b10() -> str:
    x = 8888
    v = [0] * 400
    for i in range(400):
        x = lcg(x)
        v[i] = (x >> 16) % 1000
    total, maxw = 0, -1
    for i in range(0, 400 - 40 + 1):
        s = sum(v[i + j] for j in range(40))
        total += s
        maxw = max(maxw, s)
    return f"b10 window_sum: N=400 W=40 total={total} max={maxw}"


REFERENCES = {
    "b01_sum_array": ref_b01,
    "b02_matrix_mul": ref_b02,
    "b03_crc32": ref_b03,
    "b04_string_search": ref_b04,
    "b05_bubble_sort": ref_b05,
    "b06_grayscale": ref_b06,
    "b07_fib": ref_b07,
    "b08_dot_product": ref_b08,
    "b09_strlen_loop": ref_b09,
    "b10_window_sum": ref_b10,
}

_DYNAMIC_BAND = (10**4, 5 * 10**6)  # docs/06 §3 规模要求（O0）


def main() -> int:
    EXPECTED.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    failures = []
    print(f"{'benchmark':<22} {'dyn insns':>10} {'wall_s':>7}  verdict")
    for name, ref_fn in REFERENCES.items():
        src = BM_DIR / f"{name}.c"
        if not src.is_file():
            failures.append(f"{name}: 源码缺失 {src}")
            continue
        r = run_checkup(src, arch="rv64gc", opt="O0", benchmark=name,
                        verify_determinism=True)
        if not r.ok:
            failures.append(f"{name}: pipeline state={r.state} err={r.error!r}")
            print(f"{name:<22} {'-':>10} {'-':>7}  FAIL state={r.state}")
            continue

        actual = r.stdout.replace("\r\n", "\n").strip()
        expected = ref_fn()
        if actual != expected:
            failures.append(
                f"{name}:\n  实测   = {actual!r}\n  参考实现 = {expected!r}"
            )
            print(f"{name:<22} {'-':>10} {'-':>7}  FAIL 校验和不一致")
            continue

        dyn = r.metrics["dynamic"]["total"]
        band = "ok" if _DYNAMIC_BAND[0] <= dyn <= _DYNAMIC_BAND[1] else "OUT-OF-BAND"
        (EXPECTED / f"{name}.out").write_text(r.stdout, encoding="utf-8", newline="\n")
        (SNAPSHOTS / f"{name}.json").write_text(
            json.dumps(r.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        wall = r.metrics["run"]["wall_time_median_s"]
        print(f"{name:<22} {dyn:>10} {wall:>7.2f}  frozen ({band})")

    if failures:
        print("\n=== 冻结失败 ===")
        for f in failures:
            print(f)
        return 1
    print("\n全部 10 个基准：参考实现比对一致，golden + 快照已冻结。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
