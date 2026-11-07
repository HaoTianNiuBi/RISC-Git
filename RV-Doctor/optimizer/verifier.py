"""FR5：自动验证闭环（docs/03 §5.9，docs/06 §6 状态机，M5 核心创新）。

状态机（docs/06 §6）：

    PROPOSED → COMPILED → TESTED → MEASURED → ACCEPTED
                   ↘            ↘           ↘
                  REJECTED（任何一步失败；恢复原版文件，保留全部失败证据）

判定（docs/02 FR5）：编译成功 且 stdout 与 golden 一致 且（动态指令数下降
或 能耗分下降）→ ACCEPTED；否则 REJECTED。

铁律：
- 所有判定数字来自 compile_source / run_baremetal / parse_trace / aggregate
  的实测产物；候选子目录独立落盘，证据链完整可复核；
- 原版文件永不覆盖：候选在 workdir/candidates/<id>/ 内编译运行；仅当调用方
  传入 apply_to（流水线把候选应用到该路径）时，REJECT 才恢复其原内容
  （rollback_done=True），恢复前先快照；
- 任何异常折叠为 REJECTED 并落盘现场（verdict.json + 已有产物），不静默吞错；
- 动态指令统计口径 = 程序区（metrics.aggregate 已按 DRAM_BASE 剔除引导 ROM）；
- MEASURED 转移做确定性校验：同一 ELF 连续 2 次运行动态指令数必须完全一致
  （docs/06 §5.1），不一致即 NONDETERMINISTIC 拒绝。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from compiler.riscv_compile import compile_source
from profiler.metrics import aggregate
from profiler.runner import run_baremetal
from profiler.trace_parser import parse_trace

VERDICT_NAME = "verdict.json"
ELF_NAME = "app.elf"

#: MEASURED 转移的确定性校验运行次数（docs/06 §5.1）
DETERMINISM_RUNS = 2

_BASELINE_REQUIRED_KEYS = ("stdout", "metrics", "arch", "opt")


# ---------------------------------------------------------------------------
# stdout 比对（docs/06 §4：统一换行；忽略末尾至多一个换行差异）
# ---------------------------------------------------------------------------

def stdout_diff(actual: str, expected: str) -> dict:
    """按 docs/06 §4 规则逐字符比对，返回 {identical, first_diff_line, excerpt}。"""
    a_body = actual.replace("\r\n", "\n").replace("\r", "\n")
    e_body = expected.replace("\r\n", "\n").replace("\r", "\n")
    if a_body != e_body:
        # 仅一侧多出恰好一个末尾换行 → 视为一致（"忽略末尾至多一个换行差异"）
        if a_body == e_body + "\n" or e_body == a_body + "\n":
            a_body = e_body
    if a_body == e_body:
        return {"identical": True, "first_diff_line": None, "excerpt": ""}
    a_lines, e_lines = a_body.split("\n"), e_body.split("\n")
    line_no = None
    for i in range(max(len(a_lines), len(e_lines))):
        a = a_lines[i] if i < len(a_lines) else None
        e = e_lines[i] if i < len(e_lines) else None
        if a != e:
            line_no = i + 1
            break
    lo = max(0, (line_no or 1) - 1)
    excerpt = (
        "expected: " + "\n".join(e_lines[lo : lo + 3])[:400] + "\n"
        "actual:   " + "\n".join(a_lines[lo : lo + 3])[:400]
    )
    return {"identical": False, "first_diff_line": line_no, "excerpt": excerpt}


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")
    if slug == s and slug.strip("."):
        return slug
    safe = slug if slug.strip(".") else "cand"
    return f"{safe}-{hashlib.sha256(s.encode('utf-8')).hexdigest()[:8]}"


def _empty_static() -> dict:
    return {"total_instructions": 0, "by_mnemonic": {}, "functions": [], "text_bytes": 0}


def _aggregate_from(trace_path: Path, run_result, baseline: dict) -> dict:
    stats = parse_trace(trace_path)
    meta = baseline.get("meta") or {}
    return aggregate(
        baseline.get("static_stub") or _empty_static(),
        stats,
        {
            "task_id": baseline.get("task_id", ""),
            "arch": meta.get("arch", ""),
            "opt": meta.get("opt", ""),
            "toolchain": meta.get("toolchain", ""),
            "qemu": meta.get("qemu", ""),
            "stdout": run_result.stdout,
            "exit_code": run_result.exit_code,
            "stdout_truncated": run_result.stdout_truncated,
            "weights": (baseline.get("metrics") or {}).get("energy", {}).get("weights_used"),
        },
    )


def _save(verdict: dict, cand_dir: Path) -> dict:
    (cand_dir / VERDICT_NAME).write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return verdict


def _reject(verdict: dict, reason: str) -> dict:
    verdict["state"] = "REJECTED"
    verdict["trace"].append("REJECTED")
    verdict["failure_reason"] = reason
    return verdict


def _advance(verdict: dict, state: str) -> None:
    verdict["state"] = state
    verdict["trace"].append(state)


def _finish_reject(verdict: dict, cand_dir: Path, apply_to: Path | None,
                   snapshot: bytes | None, reason: str) -> dict:
    """回滚（如适用）→ 标记 REJECTED → 落盘 verdict.json。"""
    if snapshot is not None and apply_to is not None:
        apply_to.write_bytes(snapshot)
        verdict["rollback_done"] = True
    return _save(_reject(verdict, reason), cand_dir)


def verify(
    candidate_src: Path,
    baseline: dict,
    workdir: Path,
    apply_to: Path | None = None,
    compile_timeout: float = 60.0,
    run_timeout: float = 120.0,
) -> dict:
    """对单个候选修改执行完整验证闭环，返回 verdict dict 并落盘 verdict.json。

    baseline 必备键：
        stdout: 原版/golden stdout 文本（行为比对基准）
        metrics: 原版 MetricsReport dict（性能比对基准）
        arch / opt: 编译目标（如 "rv64i" / "O0"）
    可选键：task_id、suggestion_id、meta（toolchain/qemu/arch/opt 回填指标）、
        static_stub（原版静态统计，缺省为零值——静态数不参与判定）。
    """
    workdir = Path(workdir)
    cand_dir = workdir / "candidates" / _slug(baseline.get("suggestion_id", "") or "cand")
    if cand_dir.exists():  # 同名候选重跑：清空旧证据，目录内产物只属于本次
        shutil.rmtree(cand_dir)
    cand_dir.mkdir(parents=True)

    before_metrics = baseline.get("metrics") or {}
    before_dynamic = before_metrics.get("dynamic") or {}
    before_energy = before_metrics.get("energy") or {}
    verdict = {
        "task_id": baseline.get("task_id", ""),
        "suggestion_id": baseline.get("suggestion_id", ""),
        "state": "PROPOSED",
        "trace": ["PROPOSED"],
        "checks": {"compiled": False, "stdout_identical": False, "instr_reduced": False},
        "before": {
            "dynamic.total": before_dynamic.get("total"),
            "energy.score": before_energy.get("score"),
        },
        "after": {"dynamic.total": None, "energy.score": None},
        "rollback_done": False,
        "promoted": False,
        "failure_reason": None,
        "candidate_dir": str(cand_dir),
        "additive": {},
    }

    snapshot: bytes | None = None
    src = Path(candidate_src)

    try:
        # ---- 输入校验
        missing = [k for k in _BASELINE_REQUIRED_KEYS if k not in baseline]
        if missing:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"baseline 缺少必备键: {', '.join(missing)}")
        diagnostics = (before_metrics.get("meta") or {}).get("diagnostics") or {}
        if diagnostics.get("trace_truncated") or diagnostics.get("stdout_truncated"):
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   "baseline 输出或 trace 已截断，无法可靠验证候选")
        if not src.is_file():
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"候选源码不存在: {src}")

        # ---- PROPOSED：候选就位（apply_to 先快照，供 REJECT 回滚）
        if apply_to is not None:
            apply_to = Path(apply_to)
            snapshot = apply_to.read_bytes()
            shutil.copyfile(src, apply_to)
        cand_src = cand_dir / "candidate.c"
        shutil.copyfile(src, cand_src)
        verdict["additive"]["candidate_source_sha256"] = hashlib.sha256(
            cand_src.read_bytes()
        ).hexdigest()

        # ---- COMPILED
        cres = compile_source(cand_src, baseline["arch"], baseline["opt"], cand_dir,
                              timeout=compile_timeout)
        if not cres.ok:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"编译失败（rc={cres.returncode}）：{cres.stderr_tail[-400:]}")
        verdict["checks"]["compiled"] = True
        _advance(verdict, "COMPILED")

        # ---- TESTED：首轮运行即比对 stdout（golden = baseline["stdout"]）
        # 候选版真实静态统计：报告的 after 侧静态行必须来自候选 ELF 本身的
        # 反汇编，不能沿用 baseline 的 static_stub（端到端巡检发现的 bug）
        from compiler.disassemble import disassemble as _disassemble
        from compiler.disassemble import static_stats as _static_stats

        cstat = _static_stats(_disassemble(cres.elf, cand_dir))
        baseline = {
            **baseline,
            "static_stub": {
                "total_instructions": cstat.total_instructions,
                "by_mnemonic": cstat.by_mnemonic,
                "functions": cstat.functions,
                "text_bytes": cstat.text_bytes,
            },
        }

        rres = run_baremetal(cres.elf, cand_dir, timeout=run_timeout)
        if rres.timed_out:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"运行超时（>{run_timeout:g}s）")
        if not rres.ok:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"运行失败：rc={rres.exit_code}，stderr: {rres.stderr_tail[-300:]}")
        if not rres.trace_path:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   "运行未产出 trace 日志（无法统计动态指令）")
        if rres.stdout_truncated:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   "程序 stdout 超过 1 MiB 上限，无法进行完整行为比对")
        diff = stdout_diff(rres.stdout, baseline["stdout"])
        verdict["additive"]["stdout_diff"] = diff
        if not diff["identical"]:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"输出不一致（首个差异行 {diff['first_diff_line']}）")
        verdict["checks"]["stdout_identical"] = True
        _advance(verdict, "TESTED")

        # ---- MEASURED：连续 DETERMINISM_RUNS 次运行，动态指令数必须完全一致
        reports = [_aggregate_from(rres.trace_path, rres, baseline)]
        if reports[0]["meta"]["diagnostics"]["trace_truncated"]:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   "首次运行 trace 日志被截断，无法统计完整动态指令")
        if reports[0]["dynamic"]["total"] <= 0:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   "首次运行 trace 日志没有程序区执行记录")
        for i in range(DETERMINISM_RUNS - 1):
            r2 = run_baremetal(cand_dir / ELF_NAME, cand_dir, timeout=run_timeout)
            if r2.timed_out or not r2.ok or not r2.trace_path:
                return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                       f"确定性校验第 {i + 2} 次运行失败或无 trace")
            if r2.stdout_truncated:
                return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                       f"确定性校验第 {i + 2} 次 stdout 超过 1 MiB 上限")
            if stdout_diff(r2.stdout, baseline["stdout"])["identical"] is False:
                return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                       f"确定性校验第 {i + 2} 次输出不一致")
            reports.append(_aggregate_from(r2.trace_path, r2, baseline))
            if reports[-1]["meta"]["diagnostics"]["trace_truncated"]:
                return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                       f"确定性校验第 {i + 2} 次 trace 日志被截断")
            if reports[-1]["dynamic"]["total"] <= 0:
                return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                       f"确定性校验第 {i + 2} 次 trace 日志没有程序区执行记录")
        totals = {r["dynamic"]["total"] for r in reports}
        by_classes = {
            tuple(sorted((r["dynamic"].get("by_class") or {}).items()))
            for r in reports
        }
        if len(totals) != 1 or len(by_classes) != 1:
            return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                                   f"确定性校验失败（NONDETERMINISTIC）：动态指标不一致 "
                                   f"totals={sorted(totals)} by_class={sorted(by_classes)}（docs/06 §5.1）")
        after = reports[0]
        after_total = after["dynamic"]["total"]
        after_energy = after["energy"]["score"]
        verdict["after"] = {"dynamic.total": after_total, "energy.score": after_energy}
        verdict["additive"]["after_by_class"] = after["dynamic"]["by_class"]
        # 证据链：候选版完整 MetricsReport 落盘——报告模块（reports/report.py）
        # 的 after 侧只吃 JSON（docs/03 数据流步 10），没有它就出不了真实对比表
        (cand_dir / "metrics.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        before_total = verdict["before"]["dynamic.total"]
        before_energy = verdict["before"]["energy.score"]
        instr_reduced = before_total is not None and after_total < before_total
        energy_reduced = before_energy is not None and after_energy < before_energy
        verdict["checks"]["instr_reduced"] = instr_reduced or energy_reduced
        if not verdict["checks"]["instr_reduced"]:
            return _finish_reject(
                verdict, cand_dir, apply_to, snapshot,
                f"指标未改善：动态指令 {before_total} → {after_total}，"
                f"能耗分 {before_energy} → {after_energy}",
            )
        _advance(verdict, "MEASURED")

        # ---- ACCEPTED
        _advance(verdict, "ACCEPTED")
        verdict["additive"]["improvement"] = {
            "instr_delta": (after_total - before_total) if before_total is not None else None,
            "energy_delta": (after_energy - before_energy) if before_energy is not None else None,
        }
    except Exception as exc:  # 任何异常：落盘现场，折叠为 REJECTED，绝不静默吞错
        return _finish_reject(verdict, cand_dir, apply_to, snapshot,
                               f"验证器内部异常: {type(exc).__name__}: {exc}")

    return _save(verdict, cand_dir)


def verify_many(
    candidates: list[tuple[str, Path]],
    baseline: dict,
    workdir: Path,
    **kwargs,
) -> tuple[dict | None, list[dict]]:
    """验证多个候选；全部通过时仅"晋升"动态指令数最小者（docs/06 §6：只保留一个最优）。

    返回 (best_verdict | None, all_verdicts)。promoted=True 的候选源码由
    pipeline 采用；其余候选即使 ACCEPTED 也 promoted=False。
    """
    verdicts: list[dict] = []
    for sug_id, path in candidates:
        bl = dict(baseline)
        bl["suggestion_id"] = sug_id
        verdicts.append(verify(path, bl, workdir, **kwargs))
    accepted = [v for v in verdicts if v["state"] == "ACCEPTED"]
    best = None
    if accepted:
        best = min(accepted, key=lambda v: v["after"]["dynamic.total"])
        best["promoted"] = True
        # verify() 只在候选明确传入 apply_to 时修改工作副本；多候选场景
        # 必须在选出最优者后再应用一次，不能让最后一个候选覆盖最优结果。
        apply_to = kwargs.get("apply_to")
        if apply_to is not None:
            best_src = Path(best["candidate_dir"]) / "candidate.c"
            shutil.copyfile(best_src, apply_to)
        best_dir = Path(best["candidate_dir"])
        _save(best, best_dir)
    return best, verdicts
