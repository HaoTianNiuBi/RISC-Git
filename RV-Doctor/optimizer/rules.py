"""FR4：固定规则分析引擎（R1~R9，规则编号见 docs/03 §5.7，对应基准见 docs/06 §3）。

铁律（docs/06 §1）：
- 只读分析，绝不修改源码；
- 证据字段必须引用真实反汇编片段或真实计数；**取不到证据就不输出该发现**；
- 置信度/预期收益是启发式评分（规则固有属性），不是实测数据——实测数字只
  出现在 evidence 里，且全部来自 objdump 反汇编、metrics.json 或 QEMU trace。

输入约定：
- disasm: compiler.disassemble.static_stats() 的产出（需含 insns 明细；
  缺失时需要指令级证据的规则 R1/R2/R3/R7/R8/R9 静默跳过）；
- metrics: docs/03 §6 的 MetricsReport dict；R4 额外读取可选键
  metrics["o2_compare"]（由 pipeline 用同码 -O2 实测后填入，本模块不编译）；
- trace: 可选 profiler.trace_parser.parse_trace() 的产出。提供后，发现项的
  evidence 会附带调用点/循环的真实动态执行计数（PC ≥ DRAM_BASE 的程序区）。
"""
from __future__ import annotations

import re
from pathlib import Path

from compiler.disassemble import InsnStat, StaticStats
from profiler.metrics import DRAM_BASE, classify

# ---------------------------------------------------------------------------
# 阈值与分类表（调参依据见 tests/test_rules.py 的实测用例）
# ---------------------------------------------------------------------------

THRESHOLDS = {
    "r2_redundant_loop_loads": 2,     # 同一 (基址,偏移) 在循环体内 ≥2 条不同 load
    "r3_tiny_func_insns": 4,          # "微小函数"指令数上限（O1+；O0 见下）
    "r3_tiny_func_insns_o0": 24,      # O0 下每个函数都有完整序言/尾声（实测 ch_eq=22 条），
                                      # 绝对指令数判定失效，阈值按优化等级自适应放宽
    "r3_min_site_exec": 50,           # 微小函数调用点的最低动态执行次数
    "r4_regression_ratio": 1.10,      # 当前动态指令数比 -O2 实测高 10% 以上
    "r5_sextw_share": 0.04,           # sext.w 占静态指令比例阈值（仅 rv64）
    "r6_branch_share": 0.20,          # 动态分支占比阈值（全局口径）。实测锚点：
                                      # hello@O0 全局 0.156（不触发），b03@O2 0.234、
                                      # b05@O2 0.286（触发）。O0 因指令稀释全部 <0.12，
                                      # 占比口径在 O0 诚实静默（见 _r6 docstring）
    "r6_loop_branch_share": 0.20,     # 循环体口径：体内分支动态占比阈值
    "r6_min_backedge": 100,           # 循环体口径：回边最低执行次数（滤冷循环）
    "r6_min_body_insns": 6,           # 循环体口径：最少指令数（滤 _start 4 条桩）
    "r7_window": 12,                  # R7 判定"同址重复 load"的窗口（指令数）
    "r7_max_gap": 12,
    "r8_interior_sp_ops": 6,          # 函数体中部 sp 相对访存次数阈值
    "r8_prologue_skip": 6,            # 函数头/尾跳过的指令数（排除正常栈帧）
    "r9_exec_floor": 1,               # 软浮点调用点至少执行过的次数（有 trace 时）
    "r1_min_site_exec": 2,            # R1 有 trace 时调用点最低执行次数（≥2 次才是循环内）
    "max_per_rule": 5,                # 每条规则最多输出发现数（防刷屏）
}

#: R1 认定为"可外提纯函数"的调用（与循环变量无关的常见库函数）
PURE_CALLS = {"strlen", "strnlen", "wcslen", "fabs", "sqrt"}
_SRC_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "do", "else"}

_LOADS = {"lb", "lbu", "lh", "lhu", "lw", "lwu", "ld", "flw", "fld"}
_STORES = {"sb", "sh", "sw", "sd", "fsw", "fsd"}
_CALLS = {"jal", "jalr"}

#: R9：libgcc 软浮点辅助函数符号白名单。故意不含 tf/td（128 位长双精度）：
#: RISC-V 的 long double 即使在 rv64gc 上也永远走软浮点（printf 内部即有），
#: 不构成"用户代码退化"信号；__multadd 等 newlib 整数辅助函数也不匹配。
_SOFTFLOAT_RE = re.compile(
    r"<__(?:(?:add|sub|mul|div)(?:sf|df|hf)(?:2|3)"
    r"|neg(?:sf|df|hf)2"
    r"|(?:fixu?)(?:sf|df|hf)(?:si|di|ti)"
    r"|(?:floatu?)(?:si|di|ti)(?:sf|df|hf)"
    r"|extendsfdf2|truncdfsf2|extendhfsf2|truncsfhf2|extenddfhf2|trunchfdf2"
    r"|(?:le|ge|lt|gt|eq|ne|unord)(?:sf|df|hf)2)>"
)
#: 带 F/D 扩展的架构（g 隐含 fd）；其余视为软浮点架构
_HARD_FLOAT_ARCH = re.compile(r"f[cd]?|gc")


def _arch_has_float(arch: str) -> bool:
    """rv64imafdc/rv32ifd/rv64gc 等含硬件浮点；rv64i/rv32i/rv64imac 等不含。"""
    return bool(_HARD_FLOAT_ARCH.search(arch or ""))
_OPERAND_MEM_RE = re.compile(r"^(?P<regs>[^()]*),\s*(?P<off>-?\d+)?\((?P<base>\w+)\)$")
_BRANCH_TARGET_RE = re.compile(r"([0-9a-fA-F]{3,16})(?:\s+<[^>]*>)?\s*$")

Strip = {  # noqa: N806 —— 源码片段清洗用
    "comment": re.compile(r"/\*.*?\*/|//[^\n]*", re.S),
}


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------


def _finding(
    rule_id: str, seq: int, title: str, location: dict, evidence: dict,
    suggestion: str, expected_gain: str, risk: str, confidence: float,
) -> dict:
    """按 docs/03 §6 的 Finding schema 组装字典。"""
    return {
        "id": f"{rule_id}-{seq:03d}",
        "source": "rule",
        "rule_id": rule_id,
        "title": title,
        "location": location,
        "evidence": evidence,
        "suggestion": suggestion,
        "expected_gain": expected_gain,
        "risk": risk,
        "confidence": confidence,
    }


def _mem_operands(operands: str) -> tuple[str, int] | None:
    """解析 `rd, off(base)` 形式的访存操作数，返回 (基址寄存器, 偏移)。"""
    m = _OPERAND_MEM_RE.match(operands.strip())
    if not m:
        return None
    off = int(m.group("off") or 0)
    return m.group("base"), off


def _branch_target(operands: str) -> int | None:
    """从分支/跳转操作数末尾解析目标地址（形如 `100002 <_start+0x2>`）。"""
    m = _BRANCH_TARGET_RE.search(operands.strip())
    if not m:
        return None
    try:
        return int(m.group(1), 16)
    except ValueError:
        return None


def _source_loops(src_text: str) -> list[dict]:
    """扫描源码中的 for/while 循环，返回循环头与循环体的行号与文本。

    说明：故意不解析 do-while（基准与用户代码中罕见），不支持预处理器展开；
    循环体注释先剥离，避免注释里的 `strlen(` 之类误触发。
    """
    loops: list[dict] = []
    n = len(src_text)
    for head in re.finditer(r"\b(for|while)\s*\(", src_text):
        depth, close = 0, -1
        for j in range(head.end() - 1, n):
            if src_text[j] == "(":
                depth += 1
            elif src_text[j] == ")":
                depth -= 1
                if depth == 0:
                    close = j
                    break
        if close < 0:
            continue
        header = Strip["comment"].sub(" ", src_text[head.end() : close])
        k = close + 1
        while k < n and src_text[k] in " \t\r\n":
            k += 1
        if k < n and src_text[k] == "{":
            depth, end = 0, -1
            for j in range(k, n):
                if src_text[j] == "{":
                    depth += 1
                elif src_text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            if end < 0:
                continue
            body = src_text[k + 1 : end]
        else:  # 无大括号的单语句体
            end = src_text.find(";", k)
            if end < 0:
                continue
            body = src_text[k:end]
        body = Strip["comment"].sub(" ", body)
        loops.append(
            {
                "line": src_text.count("\n", 0, head.start()) + 1,
                "header": header,
                "body": body,
                "body_line": src_text.count("\n", 0, k) + 1,
            }
        )
    return loops


def _disasm_loops(func_insns: list[InsnStat]) -> list[tuple[int, int]]:
    """按"回边"识别函数内的循环体，返回 (起始下标, 回边指令下标) 闭区间列表。"""
    idx_of = {ins.addr: i for i, ins in enumerate(func_insns)}
    loops: list[tuple[int, int]] = []
    for i, ins in enumerate(func_insns):
        if classify(ins.mnemonic) not in ("branch", "jump"):
            continue
        target = _branch_target(ins.operands)
        if target is None or target >= ins.addr:
            continue
        start = idx_of.get(target)
        if start is None or i - start > 4096:
            continue
        loops.append((start, i))
    return loops


def _site_exec(trace, pc: int) -> int | None:
    """调用点/指令的真实动态执行次数；trace 未提供或为引导 ROM 区时返回 None。"""
    if trace is None or pc < DRAM_BASE:
        return None
    return trace.exec_counts.get(pc)


def _call_argument(text: str, open_pos: int) -> str:
    """Return the text inside a call's parentheses, tolerating nested calls."""
    depth = 0
    for pos in range(open_pos, len(text)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : pos]
    return text[open_pos + 1 :]


def _loop_variables(header: str) -> set[str]:
    """Find variables controlled by a simple for/while header."""
    names = set(re.findall(r"\b([A-Za-z_]\w*)\s*=", header))
    names.update(re.findall(r"\b([A-Za-z_]\w*)\s*(?:\+\+|--)", header))
    return names


# ---------------------------------------------------------------------------
# 规则 R1~R9（每个函数返回 Finding 列表；证据不足返回空）
# ---------------------------------------------------------------------------


def _r1_invariant_call(ctx) -> list[dict]:
    """R1 循环不变量重复计算：源码循环（含循环头）内调用可外提纯函数，
    且反汇编中确有对该函数的调用指令。"""
    found: list[dict] = []
    seen_sites: set[int] = set()
    for loop in ctx.loops:
        loop_vars = _loop_variables(loop["header"])
        for text, line in ((loop["header"], loop["line"]), (loop["body"], loop["body_line"])):
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", text):
                fn = m.group(1)
                if fn not in PURE_CALLS:
                    continue
                # strlen(s + i) / fabs(a[i]) 等参数依赖循环变量时结果不恒定，
                # 不能给出“移出循环”的建议。
                args = _call_argument(text, m.end() - 1)
                if loop_vars and any(re.search(rf"\b{re.escape(v)}\b", args) for v in loop_vars):
                    continue
                call_line = line + text.count("\n", 0, m.start())
                for ins in ctx.call_sites_by_symbol.get(f"<{fn}>", []):
                    if _is_library_name(ins.func):
                        # 只认用户代码里的调用点：libc 内部的同名冷/热调用
                        # 与源码循环无关，混入只会制造噪声
                        continue
                    exec_n = _site_exec(ctx.trace, ins.addr)
                    if ctx.trace is not None and (
                        exec_n is None or exec_n < THRESHOLDS["r1_min_site_exec"]
                    ):
                        # 有 trace 时只保留真实执行 ≥2 次的调用点：源循环只会
                        # 对应一个热点调用点，libc 内部的同名冷调用点被排除
                        continue
                    if ins.addr in seen_sites:
                        continue
                    seen_sites.add(ins.addr)
                    ev = {
                        "callee": fn,
                        "src_line": call_line,
                        "loop_line": loop["line"],
                        "asm_snippet": ins.raw,
                        "call_pc": hex(ins.addr),
                    }
                    if exec_n is not None:
                        ev["dynamic_calls"] = exec_n
                    found.append(_finding(
                        "R1", len(found) + 1,
                        f"循环内重复调用 {fn}（结果不随迭代变化）",
                        {"file": ctx.src_name, "function": ins.func, "line": call_line},
                        ev,
                        f"将 {fn}(...) 移出循环，用局部变量保存长度/结果",
                        "high", "low", 0.85,
                    ))
                    if len(found) >= THRESHOLDS["max_per_rule"]:
                        return found
    return found


def _r2_invariant_loads(ctx) -> list[dict]:
    """R2 循环内重复读取不变内存：同一 (基址,偏移) 在同一循环体内被
    ≥2 条不同 load 指令读取（逐次迭代重复加载，本可寄存器化/外提）。"""
    found: list[dict] = []
    for fname, insns in ctx.func_insns.items():
        for start, end in ctx.disasm_loops.get(fname, []):
            group: dict[tuple[str, int], list[InsnStat]] = {}
            for ins in insns[start : end + 1]:
                if ins.mnemonic not in _LOADS:
                    continue
                mem = _mem_operands(ins.operands)
                if mem is None:
                    continue
                group.setdefault(mem, []).append(ins)
            for (base, off), sites in group.items():
                if len(sites) < THRESHOLDS["r2_redundant_loop_loads"]:
                    continue
                ev = {
                    "function": fname,
                    "address_pattern": f"{off}({base})",
                    "asm_snippet": "\n".join(s.raw for s in sites[:2]),
                    "loop_bound": [hex(insns[start].addr), hex(insns[end].addr)],
                }
                execs = [_site_exec(ctx.trace, s.addr) for s in sites[:2]]
                if all(e is not None for e in execs):
                    ev["dynamic_loads"] = execs
                found.append(_finding(
                    "R2", len(found) + 1,
                    f"循环内 {len(sites)} 条指令重复读取 {off}({base})",
                    {"file": ctx.src_name, "function": fname},
                    ev,
                    "将不变内存读取提到循环外或用寄存器暂存，减少每次迭代的 load",
                    "medium", "low", 0.7,
                ))
                if len(found) >= THRESHOLDS["max_per_rule"]:
                    return found
    return found


def _r3_hot_calls(ctx) -> list[dict]:
    """R3 高频函数调用：递归（函数调用自身）或微小函数被高频调用。

    "微小"阈值按优化等级自适应：O0 下每个函数都有完整序言/尾声（实测
    一个 `a==b` 比较函数也有 22 条指令），用 O1+ 的 4 条上限会漏掉全部
    O0 热点小函数，故 O0 放宽到 24 条。
    """
    found: list[dict] = []
    ranges = ctx.func_ranges  # fname -> (首地址, 末地址)
    opt = ctx.metrics.get("meta", {}).get("opt", "")
    tiny_cap = (
        THRESHOLDS["r3_tiny_func_insns_o0"] if opt == "O0"
        else THRESHOLDS["r3_tiny_func_insns"]
    )
    for fname, insns in ctx.func_insns.items():
        lo, hi = ranges[fname]
        for ins in insns:
            if ins.mnemonic not in _CALLS:
                continue
            target = _branch_target(ins.operands)
            if target is None:
                continue
            # (a) 直接自递归：调用目标落回本函数地址区间
            if lo <= target <= hi:
                ev = {
                    "function": fname,
                    "asm_snippet": ins.raw,
                    "call_pc": hex(ins.addr),
                    "callee_size_insns": len(insns),
                }
                exec_n = _site_exec(ctx.trace, ins.addr)
                if exec_n is not None:
                    ev["dynamic_calls"] = exec_n
                found.append(_finding(
                    "R3", len(found) + 1,
                    f"{fname} 存在自递归调用，调用栈随输入指数/深度增长",
                    {"file": ctx.src_name, "function": fname},
                    ev,
                    "改为迭代实现或加入记忆化，消除重复子问题",
                    "high", "medium", 0.8,
                ))
            else:  # (b) 微小函数未内联：被调函数极小且调用点动态执行频繁
                callee = ctx.symbol_owner.get(target)
                if callee is None:
                    continue
                csize = len(ctx.func_insns[callee])
                if csize > tiny_cap:
                    continue
                exec_n = _site_exec(ctx.trace, ins.addr)
                if exec_n is None or exec_n < THRESHOLDS["r3_min_site_exec"]:
                    continue
                found.append(_finding(
                    "R3", len(found) + 1,
                    f"微小函数 {callee}（{csize} 条指令）经调用点高频执行",
                    {"file": ctx.src_name, "function": fname},
                    {
                        "callee": callee,
                        "callee_size_insns": csize,
                        "asm_snippet": ins.raw,
                        "call_pc": hex(ins.addr),
                        "dynamic_calls": exec_n,
                    },
                    "将函数体手工内联或标记 static inline，消除调用开销",
                    "medium", "low", 0.75,
                ))
            if len(found) >= THRESHOLDS["max_per_rule"]:
                return found
    return found


def _r4_opt_level(ctx) -> list[dict]:
    """R4 编译等级不足：pipeline 用同码 -O2 实测后把结果放进
    metrics["o2_compare"]（本规则自身不编译、不运行）。"""
    cmp_ = ctx.metrics.get("o2_compare")
    if not isinstance(cmp_, dict):
        return []
    o2_total = cmp_.get("dynamic_total")
    cur_total = ctx.metrics.get("dynamic", {}).get("total")
    cur_opt = ctx.metrics.get("meta", {}).get("opt", "")
    if not isinstance(o2_total, int) or not isinstance(cur_total, int):
        return []
    if cur_opt in ("O2", "O3", "Os") or o2_total <= 0:
        return []
    ratio = cur_total / o2_total
    if ratio < THRESHOLDS["r4_regression_ratio"]:
        return []
    return [_finding(
        "R4", 1,
        f"当前 {cur_opt} 的动态指令数为 -O2 实测的 {ratio:.2f} 倍",
        {"file": ctx.src_name},
        {
            "current_opt": cur_opt,
            "current_dynamic_total": cur_total,
            "o2_dynamic_total": o2_total,
            "ratio": round(ratio, 3),
        },
        "直接改用 -O2 编译（或作为基线重测），再评估代码级优化",
        "high", "low", 0.95,
    )]


def _r5_redundant_ext(ctx) -> list[dict]:
    """R5 冗余类型转换：rv64 下 sext.w 高频出现（32/64 位宽度反复转换）。"""
    arch = ctx.metrics.get("meta", {}).get("arch", "")
    if not arch.startswith("rv64"):
        return []
    n = ctx.static.by_mnemonic.get("sext.w", 0)
    total = ctx.static.total_instructions
    if total <= 0 or n / total < THRESHOLDS["r5_sextw_share"]:
        return []
    return [_finding(
        "R5", 1,
        f"sext.w 占静态指令 {n / total:.1%}（32↔64 位宽度反复转换）",
        {"file": ctx.src_name},
        {
            "sext_w_count": n,
            "static_total": total,
            "share": round(n / total, 4),
        },
        "统一相关变量的整数宽度（如下标与长度同用 size_t/int64），减少符号扩展",
        "low", "low", 0.55,
    )]


def _r6_branch_dense(ctx) -> list[dict]:
    """R6 分支密集：可查表/无分支化/提前退出。

    两个独立触发口径（各自取证，命中其一即出发现）：
    (a) 全局动态分支占比 ≥ 阈值——适用于优化后的紧循环（实测 b03@O2=0.234、
        b05@O2=0.286 触发；O0 因每条 C 语句摊开成大量访存/地址指令，全部
        基准 <0.12，该口径在 O0 诚实静默，不硬凑阈值）；
    (b) 热循环体（回边执行 ≥100 次）内部分支动态占比 ≥ 阈值且体量 ≥6 条
        ——捕获分支主导的循环，O0 下同样受指令稀释限制。
    """
    found: list[dict] = []
    dyn = ctx.metrics.get("dynamic", {})
    total = dyn.get("total", 0)
    branch = dyn.get("by_class", {}).get("branch", 0)
    if total > 0 and branch / total >= THRESHOLDS["r6_branch_share"]:
        ev = {
            "dynamic_branch": branch,
            "dynamic_total": total,
            "branch_share": round(branch / total, 4),
        }
        hot = max(
            ((mn, c) for mn, c in ctx.static.by_mnemonic.items() if classify(mn) == "branch"),
            key=lambda x: x[1], default=None,
        )
        if hot:
            ev["top_branch_mnemonic"] = f"{hot[0]} x{hot[1]}"
        found.append(_finding(
            "R6", len(found) + 1,
            f"动态指令中分支占 {branch / total:.1%}，分支密集",
            {"file": ctx.src_name},
            ev,
            "考虑查表法、无分支化（算术掩码）或循环提前退出，减少每次迭代的判定",
            "medium", "medium", 0.7,
        ))
    # (b) 热循环体口径（需要 trace 提供逐 PC 计数）
    if ctx.trace is not None:
        for fname, insns in ctx.func_insns.items():
            if fname == "_start":
                continue
            for start, end in ctx.disasm_loops.get(fname, []):
                body = insns[start : end + 1]
                if len(body) < THRESHOLDS["r6_min_body_insns"]:
                    continue
                back_exec = _site_exec(ctx.trace, insns[end].addr)
                if back_exec is None or back_exec < THRESHOLDS["r6_min_backedge"]:
                    continue
                execs = [(_site_exec(ctx.trace, i.addr) or 0) for i in body]
                body_total = sum(execs)
                if not body_total:
                    continue
                body_branch = sum(
                    e for i, e in zip(body, execs) if classify(i.mnemonic) == "branch"
                )
                share = body_branch / body_total
                if share < THRESHOLDS["r6_loop_branch_share"]:
                    continue
                ev = {
                    "function": fname,
                    "loop_range": [hex(body[0].addr), hex(body[-1].addr)],
                    "backedge_exec": back_exec,
                    "body_branch": body_branch,
                    "body_total": body_total,
                    "branch_share": round(share, 4),
                    "asm_snippet": next(
                        i.raw for i in body if classify(i.mnemonic) == "branch"
                    ),
                }
                found.append(_finding(
                    "R6", len(found) + 1,
                    f"{fname} 的热循环（回边执行 {back_exec} 次）内部分支占 {share:.0%}",
                    {"file": ctx.src_name, "function": fname},
                    ev,
                    "考虑查表法、无分支化（算术掩码）或循环提前退出，减少每次迭代的判定",
                    "medium", "medium", 0.7,
                ))
                if len(found) >= THRESHOLDS["max_per_rule"]:
                    return found
    return found


def _r7_reload_pairs(ctx) -> list[dict]:
    """R7 重复内存访问：近邻窗口内对同一 (基址,偏移) 的重复 load。
    两次 load 之间若隔着 call（编译器必须重载的合法情形）或同址 store，
    不输出（避免误报）。"""
    found: list[dict] = []
    win = THRESHOLDS["r7_window"]
    for fname, insns in ctx.func_insns.items():
        reported: set[tuple[str, int]] = set()
        for i, ins in enumerate(insns):
            if ins.mnemonic not in _LOADS:
                continue
            mem = _mem_operands(ins.operands)
            if mem is None or mem[0] == "sp":
                continue
            for j in range(i + 1, min(i + 1 + win, len(insns))):
                other = insns[j]
                if other.mnemonic in _CALLS or other.mnemonic in ("ecall", "ebreak"):
                    break  # 调用可能改写内存，重载是编译器的正确行为
                if other.mnemonic in _STORES and _mem_operands(other.operands) == mem:
                    break  # 同址写入后重载同样是必须的
                if other.mnemonic not in _LOADS or _mem_operands(other.operands) != mem:
                    continue
                key = mem
                if key in reported:
                    break
                reported.add(key)
                ev = {
                    "function": fname,
                    "address_pattern": f"{mem[1]}({mem[0]})",
                    "asm_snippet": f"{ins.raw}\n{other.raw}",
                    "gap_insns": j - i,
                }
                execs = [_site_exec(ctx.trace, ins.addr), _site_exec(ctx.trace, other.addr)]
                if all(e is not None for e in execs):
                    ev["dynamic_loads"] = execs
                found.append(_finding(
                    "R7", len(found) + 1,
                    f"{fname} 内 {j - i} 条指令内重复读取 {mem[1]}({mem[0]})",
                    {"file": ctx.src_name, "function": fname},
                    ev,
                    "把重复读取的值存入局部变量复用，或消除指针别名（restrict/局部副本）",
                    "medium", "low", 0.65,
                ))
                break
            if len(found) >= THRESHOLDS["max_per_rule"]:
                return found
    return found


def _r8_spills(ctx) -> list[dict]:
    """R8 寄存器溢出：函数体中部（排除正常序言/尾声）大量 sp 相对访存。"""
    found: list[dict] = []
    skip = THRESHOLDS["r8_prologue_skip"]
    for fname, insns in ctx.func_insns.items():
        if len(insns) <= 2 * skip:
            continue
        interior = insns[skip:-skip]
        n = sum(
            1 for ins in interior
            if (ins.mnemonic in _LOADS or ins.mnemonic in _STORES)
            and (mem := _mem_operands(ins.operands)) is not None and mem[0] == "sp"
        )
        if n < THRESHOLDS["r8_interior_sp_ops"]:
            continue
        sample = next(
            ins for ins in interior
            if (ins.mnemonic in _LOADS or ins.mnemonic in _STORES)
            and (mem := _mem_operands(ins.operands)) is not None and mem[0] == "sp"
        )
        found.append(_finding(
            "R8", len(found) + 1,
            f"{fname} 函数体中部有 {n} 次 sp 相对访存，寄存器压力大（溢出）",
            {"file": ctx.src_name, "function": fname},
            {
                "interior_sp_ops": n,
                "function_size_insns": len(insns),
                "asm_snippet": sample.raw,
            },
            "拆分函数、减少同时活跃的局部变量，或改用更高优化等级让编译器重排",
            "medium", "medium", 0.7,
        ))
        if len(found) >= THRESHOLDS["max_per_rule"]:
            return found
    return found


def _r9_softfloat(ctx) -> list[dict]:
    """R9 浮点软浮点退化：无 F/D 扩展架构上浮点运算退化为 __mulsf3 等调用。
    带 F/D 扩展的架构（含 rv64gc）上不做此判定——长双精度在 RISC-V 上
    即使硬浮点架构也走软浮点，属工具链常态而非代码问题。"""
    if _arch_has_float(ctx.metrics.get("meta", {}).get("arch", "")):
        return []
    found: list[dict] = []
    seen: set[int] = set()
    for ins in ctx.insns:
        if ins.mnemonic not in _CALLS:
            continue
        m = _SOFTFLOAT_RE.search(ins.operands)
        if not m or ins.addr in seen:
            continue
        seen.add(ins.addr)
        sym = m.group(0).strip("<>")
        ev = {
            "function": ins.func,
            "callee": sym,
            "asm_snippet": ins.raw,
            "call_pc": hex(ins.addr),
        }
        exec_n = _site_exec(ctx.trace, ins.addr)
        if exec_n is not None and exec_n >= THRESHOLDS["r9_exec_floor"]:
            ev["dynamic_calls"] = exec_n
        found.append(_finding(
            "R9", len(found) + 1,
            f"浮点运算退化为软浮点调用 {sym}",
            {"file": ctx.src_name, "function": ins.func},
            ev,
            "改用定点/整数近似（如移位加权），或选择带 F 扩展的架构（rv64gc）",
            "high", "low", 0.95,
        ))
        if len(found) >= THRESHOLDS["max_per_rule"]:
            break
    return found


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


class _Ctx:
    """analyze() 的共享上下文（全部只读）。"""

    def __init__(
        self, src: Path, disasm: StaticStats, metrics: dict, trace
    ) -> None:
        self.metrics = metrics
        self.static = disasm
        self.trace = trace
        self.src_path = Path(src)
        self.src_name = self.src_path.name
        try:
            self.src_text = self.src_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.src_text = ""
        self.loops = _source_loops(self.src_text) if self.src_text else []

        self.insns: list[InsnStat] = list(getattr(disasm, "insns", None) or [])
        self.func_insns: dict[str, list[InsnStat]] = {}
        for ins in self.insns:
            if ins.func:
                self.func_insns.setdefault(ins.func, []).append(ins)
        self.func_ranges = {
            name: (fns[0].addr, fns[-1].addr)
            for name, fns in self.func_insns.items()
        }
        # 被调函数入口地址 -> 函数名：直接调用 jal 的目标即函数入口，
        # 用于 R3 的微小函数判定（间接调用 jalr 无符号注释，不参与）
        self.symbol_owner: dict[int, str] = {
            lo: name for name, (lo, _hi) in self.func_ranges.items()
        }
        self.call_sites_by_symbol: dict[str, list[InsnStat]] = {}
        for ins in self.insns:
            if ins.mnemonic in _CALLS:
                for sym in re.findall(r"<([^>]+)>", ins.operands):
                    base = sym.split("+")[0].strip()
                    self.call_sites_by_symbol.setdefault(f"<{base}>", []).append(ins)

        self.disasm_loops = {
            name: _disasm_loops(fns) for name, fns in self.func_insns.items()
        }


_RULES = (
    _r1_invariant_call, _r2_invariant_loads, _r3_hot_calls, _r4_opt_level,
    _r5_redundant_ext, _r6_branch_dense, _r7_reload_pairs, _r8_spills,
    _r9_softfloat,
)


def _is_library_name(name: str | None) -> bool:
    """库函数启发式：newlib/libgcc 内部符号几乎都以 _ / __ 开头。

    仅用于给发现打 scope 标注（用户代码优先展示），不影响是否输出——
    库代码里的发现同样是真实证据（如 printf 拖入的 _vfprintf_r 体积）。
    """
    return not name or name.startswith("_")


def _tag_scope(findings: list[dict]) -> list[dict]:
    for f in findings:
        fn = f.get("location", {}).get("function") or f.get("evidence", {}).get("function")
        f["location"]["scope"] = "library" if _is_library_name(fn) else "user"
    findings.sort(key=lambda f: (0 if f["location"]["scope"] == "user" else 1,
                                 f["rule_id"], f["id"]))
    return findings


def analyze(
    src: Path,
    disasm: StaticStats,
    metrics: dict,
    trace=None,  # 可选 profiler.trace_parser.TraceStats，提供动态执行计数证据
) -> list[dict]:
    """对一次"体检"产物做只读规则分析，返回 Finding 列表（docs/03 §6 schema）。

    每条规则独立取证：反汇编/计数取不到证据时该规则静默返回空，
    绝不输出无证据的"感觉"。只读分析，绝不修改源码。发现按
    用户代码 → 库代码排序（scope 标注只是展示优先级，不删证据）。
    """
    ctx = _Ctx(src, disasm, metrics, trace)
    findings: list[dict] = []
    for rule in _RULES:
        findings.extend(rule(ctx))
    return _tag_scope(findings)
