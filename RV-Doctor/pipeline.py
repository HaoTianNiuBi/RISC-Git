"""Pipeline 编排层（docs/03 §2/§3，FR1~FR3 串联；UI 只调用本模块）。

职责：
- 任务目录管理：每次体检使用独立 workdir/<task_id>/，失败产物也保留（证据链）；
- 步骤编排：接收源码 → 编译 → 反汇编/静态统计 → 运行 → trace 解析 → 指标聚合；
- 优化阶段编排（run_optimize，docs/11 D1 桥接层）：规则取证 → LLM 建议 →
  候选落盘 → 验证闭环（verifier）→ 对比报告（report），同样只吃落盘证据；
- 异常包装：业务失败以 CheckupResult.state 结构化返回，不向 UI 抛业务异常；
  调用方参数错误（非法 arch/opt 等）抛 PipelineError；
- 状态推进：DONE / COMPILE_FAILED / RUN_FAILED / PARSE_FAILED /
  NONDETERMINISTIC / ERROR（06 文档 §5/§6 的子集，M5 验证闭环在此基础上扩展）。

指标红线（06 文档 §1）：界面展示的每个数字都来自本模块落盘的 JSON，
无证据不展示。
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from compiler import riscv_compile
from compiler.config import load_config
from compiler.disassemble import (
    DisassembleError,
    StaticStats,
    disassemble,
    static_stats,
)
from optimizer import llm_agent, rules, verifier
from profiler import runner
from profiler.metrics import DRAM_BASE, aggregate
from profiler.trace_parser import parse_trace
from reports.report import render_comparison_report

REPO_ROOT = Path(__file__).resolve().parent

#: 源码大小上限（输入爆炸对策，docs/03 §9）
MAX_SRC_BYTES = 1 << 20

#: trace 解析字节数上限（config [limits] trace_max_bytes 可覆盖）
DEFAULT_TRACE_MAX_BYTES = 2**30

STATE_DONE = "DONE"
STATE_COMPILE_FAILED = "COMPILE_FAILED"
STATE_RUN_FAILED = "RUN_FAILED"
STATE_PARSE_FAILED = "PARSE_FAILED"
STATE_NONDETERMINISTIC = "NONDETERMINISTIC"
STATE_ERROR = "ERROR"


class PipelineError(RuntimeError):
    """调用方错误（非法参数、源码缺失等），不进入任务目录流程。"""


@dataclass
class StepRecord:
    name: str
    ok: bool
    detail: str = ""
    duration_s: float = 0.0
    artifacts: list[Path] = field(default_factory=list)


@dataclass
class CheckupResult:
    task_id: str
    state: str
    steps: list[StepRecord] = field(default_factory=list)
    workdir: Path | None = None
    metrics: dict | None = None        # DONE / NONDETERMINISTIC 时有值
    stdout: str = ""
    error: str | None = None
    arch: str = ""
    opt: str = ""

    @property
    def ok(self) -> bool:
        return self.state == STATE_DONE

    def summary(self) -> dict:
        """UI/测试用的紧凑视图（metrics 全量见 workdir/metrics.json）。"""
        return {
            "task_id": self.task_id,
            "state": self.state,
            "ok": self.ok,
            "arch": self.arch,
            "opt": self.opt,
            "error": self.error,
            "stdout": self.stdout,
            "steps": [
                {"name": s.name, "ok": s.ok, "detail": s.detail,
                 "duration_s": round(s.duration_s, 3)}
                for s in self.steps
            ],
            "metrics": self.metrics,
            "workdir": str(self.workdir) if self.workdir else None,
        }


def new_task_id() -> str:
    """ASCII 任务号：t-YYYYMMDD-HHMMSS-xxxx（目录名进入 gcc/QEMU 参数，必须 ASCII）。"""
    return "t-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


_task_name_re = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_name(name: str, fallback: str = "app.c") -> str:
    """上传文件名仅保留 ASCII 安全字符（中文路径会击穿 gcc/QEMU，04 FAQ）。

    连续非法字符折叠为一个下划线；清洗后不含任何字母数字（如 "///" → "_"）
    时回退 fallback。"""
    cleaned = _task_name_re.sub("_", name).strip()
    if not cleaned or not re.search(r"[A-Za-z0-9]", cleaned) or cleaned.startswith("."):
        return fallback
    return cleaned


_gcc_version_cache: dict[str, str] = {}


def gcc_version(gcc_exe: Path) -> str:
    key = str(gcc_exe)
    if key not in _gcc_version_cache:
        proc = subprocess.run(
            [key, "--version"], capture_output=True, timeout=30.0
        )
        first = proc.stdout.decode("utf-8", errors="replace").splitlines()
        _gcc_version_cache[key] = first[0].strip() if first else "unknown"
    return _gcc_version_cache[key]


def tool_versions(arch: str) -> dict[str, str]:
    """meta 用的工具链版本（gcc/qemu 实测值，06 文档证据链要求）。"""
    from compiler.config import resolve_tool

    bitness = 32 if arch == "rv32i" else 64
    versions = {"qemu": "unavailable"}
    try:
        versions["qemu"] = runner.qemu_version(runner.resolve_qemu(bitness))
    except (runner.RunnerError, OSError):
        pass
    try:
        versions["toolchain"] = gcc_version(resolve_tool("gcc", load_config()))
    except Exception:
        versions["toolchain"] = "unavailable"
    return versions


def stdout_matches(actual: str, golden: str) -> tuple[bool, str]:
    """golden 比对（06 文档 §4）：统一换行后忽略**末尾至多一个**换行差异。

    返回 (是否一致, 首个差异描述)。"""
    a = actual.replace("\r\n", "\n")
    g = golden.replace("\r\n", "\n")
    if a.endswith("\n"):
        a = a[:-1]
    if g.endswith("\n"):
        g = g[:-1]
    if a == g:
        return True, ""
    a_lines, g_lines = a.split("\n"), g.split("\n")
    for i in range(max(len(a_lines), len(g_lines))):
        al = a_lines[i] if i < len(a_lines) else "<EOF>"
        gl = g_lines[i] if i < len(g_lines) else "<EOF>"
        if al != gl:
            return False, f"line {i + 1}: actual={al[:200]!r} golden={gl[:200]!r}"
    return False, "mismatch"


def _write_trace_stats(path: Path, stats) -> None:
    payload = {
        "total_exec": stats.total_exec,
        "pc_mnemonic": {f"{pc:#x}": m for pc, m in sorted(stats.pc_mnemonic.items())},
        "exec_counts": {f"{pc:#x}": c for pc, c in sorted(stats.exec_counts.items())},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def run_checkup(
    source: Path | str,
    *,
    arch: str = "rv64gc",
    opt: str = "O0",
    benchmark: str = "",
    workroot: Path | None = None,
    timeouts: dict | None = None,
    verify_determinism: bool = False,
) -> CheckupResult:
    """一键体检：源码 → 编译 → 反汇编 → 运行 → trace 解析 → metrics.json。

    source 为源码文本或 .c 文件路径。verify_determinism=True 时追加第二次
    运行并做确定性校验（06 文档 §5.1，不一致 → NONDETERMINISTIC）。
    """
    if arch not in riscv_compile.ARCH_MAP:
        raise PipelineError(f"非法架构 {arch!r}，可选：{', '.join(riscv_compile.ARCH_MAP)}")
    if opt not in riscv_compile.OPT_LEVELS:
        raise PipelineError(f"非法优化等级 {opt!r}，可选：{', '.join(riscv_compile.OPT_LEVELS)}")

    cfg = load_config()
    tcfg = dict(cfg.get("timeouts", {}))
    if timeouts:
        tcfg.update(timeouts)
    trace_cap = int(cfg.get("limits", {}).get("trace_max_bytes", DEFAULT_TRACE_MAX_BYTES))

    task_id = new_task_id()
    workdir = (workroot or (REPO_ROOT / "workdir")) / task_id
    # 中文路径守卫（04 FAQ）：gcc/QEMU 在非 ASCII 路径下会失败（QEMU 报
    # "Error opening logfile"），提前给出可操作的错误而非让工具链玄学报错。
    if not str(workdir).isascii():
        raise PipelineError(
            f"工作目录含非 ASCII 字符: {workdir}\n"
            "gcc/QEMU 无法在中文路径下工作，请将 workroot 指向纯 ASCII 路径。"
        )
    result = CheckupResult(task_id=task_id, state=STATE_ERROR, workdir=workdir,
                           arch=arch, opt=opt)

    def step(name: str, ok: bool, detail: str = "", dt: float = 0.0,
             artifacts: list[Path] | None = None) -> StepRecord:
        rec = StepRecord(name, ok, detail, dt, artifacts or [])
        result.steps.append(rec)
        return rec

    try:
        # ---- 1. 接收源码 ---------------------------------------------------
        t0 = time.perf_counter()
        src_dir = workdir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        src_path = src_dir / "app.c"
        if isinstance(source, Path):
            if not source.is_file():
                raise PipelineError(f"源码文件不存在: {source}")
            data = source.read_bytes()
        else:
            source_text = str(source)
            source_path = Path(source_text)
            # 保持源码字符串调用方式，同时支持文档声明的字符串路径输入。
            data = source_path.read_bytes() if source_path.is_file() else source_text.encode("utf-8")
        if len(data) > MAX_SRC_BYTES:
            raise PipelineError(f"源码超过 {MAX_SRC_BYTES} 字节上限")
        src_path.write_bytes(data)
        step("receive", True, f"{len(data)} bytes -> {src_path.name}",
             time.perf_counter() - t0, [src_path])

        # ---- 2. 编译 ---------------------------------------------------
        t0 = time.perf_counter()
        cr = riscv_compile.compile_source(
            src_path, arch, opt, workdir, timeout=float(tcfg.get("compile", 60.0))
        )
        step("compile", cr.ok, ("" if cr.ok else cr.stderr_tail[:500]),
             time.perf_counter() - t0, [cr.log_path] if cr.log_path else [])
        if not cr.ok or cr.elf is None:
            result.state = STATE_COMPILE_FAILED
            result.error = cr.stderr_tail[:2000] or f"gcc rc={cr.returncode}"
            return result

        # ---- 3. 反汇编 + 静态统计 ----------------------------------------
        t0 = time.perf_counter()
        try:
            disasm_path = disassemble(cr.elf, workdir)
            static = static_stats(disasm_path)
        except DisassembleError as exc:
            step("disasm", False, str(exc), time.perf_counter() - t0)
            result.state = STATE_ERROR
            result.error = f"disassemble failed: {exc}"
            return result
        step("disasm", True,
             f"{static.total_instructions} insns, {static.text_bytes}B text",
             time.perf_counter() - t0, [disasm_path])

        # ---- 4. 运行（QEMU 半主机 + trace）-------------------------------
        t0 = time.perf_counter()
        rr = runner.run_baremetal(
            cr.elf, workdir, timeout=float(tcfg.get("run", 120.0))
        )
        detail = "ok" if rr.ok else (
            "timed out" if rr.timed_out else f"rc={rr.exit_code} {rr.stderr_tail[:200]}"
        )
        step("run", rr.ok, detail, time.perf_counter() - t0,
             [workdir / "stdout.txt", rr.trace_path] if rr.trace_path else [])
        result.stdout = rr.stdout
        if not rr.ok or rr.trace_path is None:
            result.state = STATE_RUN_FAILED
            result.error = detail
            return result

        # ---- 5. 解析 trace ----------------------------------------------
        t0 = time.perf_counter()
        try:
            trace = parse_trace(rr.trace_path, max_bytes=trace_cap)
            if trace.truncated:
                raise ValueError("trace 日志被截断，无法统计完整动态指令")
            if not any(pc >= DRAM_BASE for pc in trace.exec_counts):
                raise ValueError("trace 日志没有程序区执行记录，无法统计动态指令")
        except Exception as exc:  # 解析器自身异常（FileNotFoundError 等）
            step("parse", False, str(exc), time.perf_counter() - t0)
            result.state = STATE_PARSE_FAILED
            result.error = str(exc)
            return result
        stats_path = workdir / "trace_stats.json"
        _write_trace_stats(stats_path, trace)
        step("parse", True, f"total_exec={trace.total_exec}",
             time.perf_counter() - t0, [stats_path])

        # ---- 6. 聚合指标 -------------------------------------------------
        t0 = time.perf_counter()
        run_meta = {
            "task_id": task_id,
            "benchmark": benchmark,
            "arch": arch,
            "opt": opt,
            **tool_versions(arch),
            "stdout": rr.stdout,
            "exit_code": rr.exit_code,
            "wall_times": [rr.wall_time_s],   # 参考值（06 §2 必须标注）
            "stdout_truncated": rr.stdout_truncated,
            "weights": cfg.get("energy", {}).get("weights"),
        }
        metrics = aggregate(static, trace, run_meta)
        metrics_path = workdir / "metrics.json"
        metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        step("aggregate", True, f"dynamic.total={metrics['dynamic']['total']}",
             time.perf_counter() - t0, [metrics_path])
        result.metrics = metrics

        # ---- 7.（可选）确定性校验：第二次运行比对 --------------------------
        if verify_determinism:
            t0 = time.perf_counter()
            det_dir = workdir / "det"
            det_dir.mkdir(parents=True, exist_ok=True)
            rr2 = runner.run_baremetal(
                cr.elf, det_dir, timeout=float(tcfg.get("run", 120.0))
            )
            if not rr2.ok or rr2.trace_path is None:
                step("determinism", False, "second run failed",
                     time.perf_counter() - t0)
                result.state = STATE_RUN_FAILED
                result.error = "determinism second run failed"
                return result
            if rr.stdout_truncated or rr2.stdout_truncated or not stdout_matches(rr2.stdout, rr.stdout)[0]:
                step("determinism", False, "两次运行输出不一致或被截断",
                     time.perf_counter() - t0, [det_dir / "stdout.txt"])
                result.state = STATE_NONDETERMINISTIC
                result.error = "两次运行输出不一致或被截断，无法确认行为一致"
                return result
            try:
                trace2 = parse_trace(rr2.trace_path, max_bytes=trace_cap)
                if trace2.truncated:
                    raise ValueError("第二次 trace 日志被截断，无法验证动态指令")
                if not any(pc >= DRAM_BASE for pc in trace2.exec_counts):
                    raise ValueError("第二次 trace 日志没有程序区执行记录")
            except Exception as exc:
                step("determinism", False, str(exc), time.perf_counter() - t0)
                result.state = STATE_PARSE_FAILED
                result.error = str(exc)
                return result
            m2 = aggregate(static, trace2, run_meta)
            same = (
                metrics["dynamic"]["total"] == m2["dynamic"]["total"]
                and metrics["dynamic"]["by_class"] == m2["dynamic"]["by_class"]
            )
            step("determinism", same,
                 f"{metrics['dynamic']['total']} vs {m2['dynamic']['total']}",
                 time.perf_counter() - t0, [det_dir / "trace.log"])
            if not same:
                result.state = STATE_NONDETERMINISTIC
                result.error = (
                    f"两次运行动态指标不一致: {metrics['dynamic']['total']} vs "
                    f"{m2['dynamic']['total']}"
                )
                return result

        result.state = STATE_DONE
        return result

    except PipelineError:
        raise
    except Exception as exc:  # 未预期异常：保留现场（workdir 已建）
        result.state = STATE_ERROR
        result.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
        return result


# ---------------------------------------------------------------------------
# 优化阶段编排（docs/11 D1 桥接层）：体检产物 → 建议 → 验证 → 报告
# ---------------------------------------------------------------------------

OPT_STATE_DONE = "DONE"
OPT_STATE_ERROR = "ERROR"


@dataclass
class OptimizeResult:
    """优化建议阶段结果（run_optimize 产出；UI 只读本结构与 summary()）。

    state：DONE（流程走完，无论是否有候选）/ ERROR（任务目录不完整等）。
    llm_mode："llm"（LLM 给出候选）| "rules"（降级：仅规则取证，无候选）。
    """

    task_id: str
    workdir: Path
    state: str
    findings: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    best_verdict: dict | None = None
    llm_mode: str = "rules"
    llm_degraded_reason: str = ""
    reports: list[Path] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == OPT_STATE_DONE

    def summary(self) -> dict:
        """UI/会话恢复用的紧凑视图（全部 JSON 可序列化）。"""
        return {
            "task_id": self.task_id,
            "workdir": str(self.workdir) if self.workdir else None,
            "state": self.state,
            "findings": self.findings,
            "suggestions": self.suggestions,
            "verdicts": self.verdicts,
            "best": (self.best_verdict or {}).get("suggestion_id"),
            "llm_mode": self.llm_mode,
            "llm_degraded_reason": self.llm_degraded_reason,
            "reports": [str(p) for p in self.reports],
            "steps": [
                {"name": s.name, "ok": s.ok, "detail": s.detail,
                 "duration_s": round(s.duration_s, 3)}
                for s in self.steps
            ],
            "error": self.error,
        }


def run_optimize(
    workdir: Path | str,
    *,
    timeouts: dict | None = None,
    max_candidates: int = 3,
    llm_cfg: "llm_agent.LlmConfig | None" = None,
) -> OptimizeResult:
    """优化阶段一键编排（FR4/FR5/FR6 串联，docs/11 D1）。

    llm_cfg：用户在界面填写的 DeepSeek 配置（LlmConfig，仅存会话内存）；
    None 或字段不全时 llm_agent.suggest 自动降级为规则引擎模式。

    输入是 run_checkup 已完成的任务目录，只读取落盘证据（metrics.json /
    disasm.txt / trace.log / stdout.txt / src/app.c），本函数自身不产生任何
    数字（docs/06 §1）。流程：

        规则取证（rules.analyze）→ LLM 建议（llm_agent.suggest，未配置自动
        降级）→ 候选源码落盘 suggestions/llm-NN.c → 验证闭环
        （verifier.verify_many：编译→输出比对→确定性双跑→指标对比，失败
        自动 REJECTED）→ ACCEPTED 候选生成对比报告（reports.report）。

    候选不覆盖用户原稿：apply_to 恒为 None，采纳候选的源码位于
    candidates/<sug_id>/candidate.c（verifier 语义，证据链可回溯）。
    """
    cfg = load_config()
    tcfg = dict(cfg.get("timeouts", {}))
    if timeouts:
        tcfg.update(timeouts)
    trace_cap = int(cfg.get("limits", {}).get("trace_max_bytes", DEFAULT_TRACE_MAX_BYTES))

    workdir = Path(workdir)
    result = OptimizeResult(task_id="", workdir=workdir, state=OPT_STATE_ERROR)

    def step(name: str, ok: bool, detail: str = "", dt: float = 0.0,
             artifacts: list[Path] | None = None) -> StepRecord:
        rec = StepRecord(name, ok, detail, dt, artifacts or [])
        result.steps.append(rec)
        return rec

    try:
        # ---- 0. 读取体检落盘证据 -------------------------------------------
        metrics_path = workdir / "metrics.json"
        if not metrics_path.is_file():
            result.error = f"任务目录缺少 metrics.json（先完成一键体检）: {workdir}"
            return result
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        diagnostics = (metrics.get("meta") or {}).get("diagnostics") or {}
        if diagnostics.get("trace_truncated") or diagnostics.get("stdout_truncated"):
            result.error = "原始体检输出或 trace 已截断，无法可靠验证候选；请重新体检"
            return result
        meta = metrics.get("meta") or {}
        result.task_id = str(meta.get("task_id", ""))

        src = workdir / "src" / "app.c"
        stdout_path = workdir / "stdout.txt"
        missing = [p.name for p in (src, stdout_path) if not p.is_file()]
        if missing:
            result.error = f"任务目录缺少 {'、'.join(missing)}（请重新体检）: {workdir}"
            return result
        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")

        # 静态明细（rules 的指令级证据原料）与动态轨迹可缺省：缺文件时按
        # 规则纪律静默降级（rules.py 输入约定），不让优化阶段整体失败
        disasm_path = workdir / "disasm.txt"
        static = (static_stats(disasm_path) if disasm_path.is_file()
                  else StaticStats(0, {}, [], 0))
        trace_path = workdir / "trace.log"
        trace = (parse_trace(trace_path, max_bytes=trace_cap)
                 if trace_path.is_file() else None)
        if trace is not None and getattr(trace, "truncated", False):
            result.error = "体检 trace 日志被截断，无法可靠生成优化证据；请重新体检"
            return result

        # ---- 1. 规则取证 + LLM 建议 ----------------------------------------
        t0 = time.perf_counter()
        findings = rules.analyze(src, static, metrics, trace)
        outcome = llm_agent.suggest(src=src, metrics=metrics,
                                    findings=findings, disasm=static,
                                    cfg=llm_cfg)
        result.findings = findings
        result.suggestions = outcome.suggestions[:max_candidates]
        result.llm_mode = "llm" if result.suggestions else "rules"
        result.llm_degraded_reason = outcome.degraded_reason
        step("optimize", True,
             f"findings={len(findings)} suggestions={len(result.suggestions)} "
             f"mode={result.llm_mode}",
             time.perf_counter() - t0)

        # ---- 2. 候选落盘（证据链：optimize.json） ---------------------------
        sug_dir = workdir / "suggestions"
        sug_dir.mkdir(parents=True, exist_ok=True)
        candidates: list[tuple[str, Path]] = []
        for i, sug in enumerate(result.suggestions):
            sug_id = f"llm-{i + 1:02d}"
            cand_path = sug_dir / f"{sug_id}.c"
            cand_path.write_text(sug.get("modified_source", ""), encoding="utf-8")
            candidates.append((sug_id, cand_path))
        (workdir / "optimize.json").write_text(json.dumps({
            "task_id": result.task_id,
            "llm_mode": result.llm_mode,
            "degraded_reason": result.llm_degraded_reason,
            "findings": findings,
            "suggestions": [
                {**sug, "suggestion_id": f"llm-{i + 1:02d}"}
                for i, sug in enumerate(result.suggestions)
            ],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- 3. 验证闭环（失败自动 REJECTED，绝不覆盖用户原稿） --------------
        t0 = time.perf_counter()
        if candidates:
            baseline = {
                "stdout": stdout_text,
                "metrics": metrics,
                "arch": str(meta.get("arch", "")),
                "opt": str(meta.get("opt", "")),
                "task_id": result.task_id,
                "static_stub": {
                    "total_instructions": metrics["static"]["total_instructions"],
                    "by_mnemonic": metrics["static"].get("by_mnemonic", {}),
                    "functions": metrics["static"].get("functions", []),
                    "text_bytes": metrics["static"].get("text_bytes", 0),
                },
                "meta": {
                    "arch": str(meta.get("arch", "")),
                    "opt": str(meta.get("opt", "")),
                    "toolchain": str(meta.get("toolchain", "")),
                    "qemu": str(meta.get("qemu", "")),
                },
            }
            best, verdicts = verifier.verify_many(
                candidates, baseline, workdir,
                apply_to=None,
                compile_timeout=float(tcfg.get("compile", 60.0)),
                run_timeout=float(tcfg.get("run", 120.0)),
            )
            result.verdicts = verdicts
            result.best_verdict = best
            accepted = sum(1 for v in verdicts if v.get("state") == "ACCEPTED")
            step("verify", True,
                 f"candidates={len(candidates)} accepted={accepted}",
                 time.perf_counter() - t0)
        else:
            step("verify", True,
                 f"无候选（mode={result.llm_mode}），跳过验证闭环", 0.0)

        # ---- 4. 对比报告（ACCEPTED 且候选目录已落盘 after metrics.json） ----
        t0 = time.perf_counter()
        for v in result.verdicts:
            if v.get("state") != "ACCEPTED":
                continue
            after_path = Path(v["candidate_dir"]) / "metrics.json"
            if not after_path.is_file():
                continue
            after = json.loads(after_path.read_text(encoding="utf-8"))
            report_md = render_comparison_report(metrics, after, v)
            out = workdir / "reports" / f"{v.get('suggestion_id', 'cand')}_report.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report_md, encoding="utf-8")
            result.reports.append(out)
        step("report", True, f"reports={len(result.reports)}",
             time.perf_counter() - t0)

        result.state = OPT_STATE_DONE
        return result

    except Exception as exc:  # 未预期异常：折叠为 ERROR，不向 UI 抛业务异常
        result.state = OPT_STATE_ERROR
        result.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
        return result
