"""FR6：优化前后对比报告生成（docs/05 P8）。

铁律（docs/06 §1 数据溯源 / README 数据红线）：
- 表格数字只能取自 before/after 两份 MetricsReport JSON 与 verdict，
  本模块只做"翻译"（格式化与变化率计算），不做任何估算；
- 变化率 = (after-before)/before，同样只由 JSON 数值推导；缺失值渲染为
  "—"，绝不编造；
- 能耗分必须带免责声明；报告必须带"TCG 不模拟缓存"边界声明与数据来源注脚
  （task_id、工具版本、产物文件名）。

输入：
- before / after：docs/03 §6 MetricsReport dict（profiler.metrics.aggregate 产出）；
- verdict：可选，optimizer.verifier.verify 的 verdict dict；为 None 时报告
  以"直接对比（未走验证闭环）"呈现，输出一致性改用两份 metrics 中的
  stdout_sha256 判定（例如跨架构同源码对比场景）；
- sources：可选 {名称: 文件路径} 数据来源注脚（before/after metrics、verdict、
  trace、build.log 等）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CLASS_LABELS = {  # docs/03 §5.5 类别顺序
    "integer": "整数",
    "memory": "访存",
    "branch": "分支",
    "jump": "跳转",
    "muldiv": "乘除",
    "float": "浮点",
    "system": "系统/其他",
}

ENERGY_DISCLAIMER = (
    "> ⚠️ 能耗估算分 = Σ(类别动态计数 × 可配置权重)，是**相对估算**，"
    "仅用于优化前后趋势对比，不替代真实硬件功耗测量。"
)
TCG_BOUNDARY = (
    "> ⚠️ 边界声明：QEMU TCG 不模拟缓存与时序，本报告的结论限定为"
    "指令数与能耗趋势的相对对比，不做绝对性能声明；\"缓存友好性\"不作为实测指标。"
)
DIRECT_COMPARE_NOTE = (
    "本报告为**直接对比**（未走验证闭环状态机）：前后指标均来自真实编译、"
    "运行与 trace 解析，输出一致性由两份 metrics 的 stdout SHA256 判定。"
)


def _fmt_int(v) -> str:
    """整数渲染：数值 → 千分位字符串；缺失 → '—'。"""
    if isinstance(v, bool) or v is None:
        return "—"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float) and v.is_integer():
        return f"{int(v):,}"
    return str(v)


def _pct(before, after) -> str:
    """变化率（%），只由两个 JSON 数值推导；无法计算时 '—'。"""
    if isinstance(before, bool) or isinstance(after, bool):
        return "—"
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return "—"
    if before == 0:
        return "—"  # 分母为 0 不定义，不硬凑
    return f"{(after - before) / before * 100:+.1f}%"


def _meta(before: dict, after: dict) -> dict:
    mb = before.get("meta") or {}
    ma = after.get("meta") or {}
    return {
        "task_id": mb.get("task_id") or ma.get("task_id") or "",
        "benchmark": mb.get("benchmark") or ma.get("benchmark") or "",
        "before_arch": mb.get("arch", ""), "after_arch": ma.get("arch", ""),
        "before_opt": mb.get("opt", ""), "after_opt": ma.get("opt", ""),
        "toolchain": mb.get("toolchain") or ma.get("toolchain") or "",
        "qemu": mb.get("qemu") or ma.get("qemu") or "",
    }


def _verdict_section(verdict: dict | None, before: dict, after: dict) -> list[str]:
    lines: list[str] = []
    if verdict is None:
        sha_b = (before.get("run") or {}).get("stdout_sha256")
        sha_a = (after.get("run") or {}).get("stdout_sha256")
        same = sha_b is not None and sha_b == sha_a
        lines += [
            "## 验证结论",
            "",
            DIRECT_COMPARE_NOTE,
            "",
            f"- 输出一致性：{'PASS（stdout SHA256 相同）' if same else 'FAIL（stdout SHA256 不同）'}",
            "",
        ]
        return lines

    lines += ["## 验证结论", "", f"- 状态：**{verdict.get('state', '—')}**"]
    trace = verdict.get("trace") or []
    if trace:
        lines.append(f"- 状态轨迹：{' → '.join(trace)}")
    checks = verdict.get("checks") or {}
    if checks:
        cell = ", ".join(
            f"{k}={'PASS' if v else 'FAIL'}" for k, v in checks.items()
        )
        lines.append(f"- 检查项：{cell}")
    if verdict.get("failure_reason"):
        lines.append(f"- 失败原因：{verdict['failure_reason']}")
    if verdict.get("state") == "REJECTED" and verdict.get("rollback_done") is not None:
        lines.append(f"- 回滚：{'已回滚到原版' if verdict['rollback_done'] else '未回滚（无 apply_to）'}")
    lines.append("")
    return lines


def render_comparison_report(
    before: dict,
    after: dict,
    verdict: dict | None = None,
    *,
    sources: dict[str, str | Path] | None = None,
    notes: str | None = None,
) -> str:
    """渲染 Markdown 对比报告。所有数字只来自两份 MetricsReport 与 verdict。"""
    meta = _meta(before, after)
    title_ctx = " / ".join(x for x in (meta["benchmark"], meta["task_id"]) if x)
    lines = [f"# RV-Doctor 优化对比报告{' — ' + title_ctx if title_ctx else ''}", ""]

    # ---- 配置（前后允许不同，例如跨架构对比） ----
    if meta["before_arch"] != meta["after_arch"] or meta["before_opt"] != meta["after_opt"]:
        lines.append(
            f"- 编译配置：优化前 `{meta['before_arch']} / -{meta['before_opt']}` → "
            f"优化后 `{meta['after_arch']} / -{meta['after_opt']}`（同源码配置变更，见结论说明）"
        )
    else:
        lines.append(f"- 编译配置：`{meta['before_arch']} / -{meta['before_opt']}`（前后一致）")
    if meta["toolchain"]:
        lines.append(f"- 工具链：{meta['toolchain']} ｜ QEMU：{meta['qemu']}")
    lines.append("")

    lines += _verdict_section(verdict, before, after)

    # ---- 指标对比表 ----
    dyn_b = before.get("dynamic") or {}
    dyn_a = after.get("dynamic") or {}
    cls_b = dyn_b.get("by_class") or {}
    cls_a = dyn_a.get("by_class") or {}
    st_b = before.get("static") or {}
    st_a = after.get("static") or {}
    en_b = before.get("energy") or {}
    en_a = after.get("energy") or {}

    lines += [
        "## 指标对比",
        "",
        "| 指标 | 优化前 | 优化后 | 变化率 |",
        "|---|---:|---:|---:|",
        f"| 动态指令总数 | {_fmt_int(dyn_b.get('total'))} | {_fmt_int(dyn_a.get('total'))} | {_pct(dyn_b.get('total'), dyn_a.get('total'))} |",
    ]
    for cls, label in CLASS_LABELS.items():
        lines.append(
            f"| {label}指令 | {_fmt_int(cls_b.get(cls))} | {_fmt_int(cls_a.get(cls))} | {_pct(cls_b.get(cls), cls_a.get(cls))} |"
        )
    lines += [
        f"| 函数调用（jal/jalr 写 ra） | {_fmt_int(dyn_b.get('calls'))} | {_fmt_int(dyn_a.get('calls'))} | {_pct(dyn_b.get('calls'), dyn_a.get('calls'))} |",
        f"| 静态指令总数 | {_fmt_int(st_b.get('total_instructions'))} | {_fmt_int(st_a.get('total_instructions'))} | {_pct(st_b.get('total_instructions'), st_a.get('total_instructions'))} |",
        f"| 代码段字节 | {_fmt_int(st_b.get('text_bytes'))} | {_fmt_int(st_a.get('text_bytes'))} | {_pct(st_b.get('text_bytes'), st_a.get('text_bytes'))} |",
        f"| 能耗估算分 | {_fmt_int(en_b.get('score'))} | {_fmt_int(en_a.get('score'))} | {_pct(en_b.get('score'), en_a.get('score'))} |",
        "",
    ]

    # ---- 结论 ----
    lines.append("## 结论")
    lines.append("")
    state = verdict.get("state") if verdict else None
    if state == "ACCEPTED":
        imp = (verdict.get("additive") or {}).get("improvement") or {}
        instr_delta = imp.get("instr_delta")
        energy_delta = imp.get("energy_delta")
        before_instr = dyn_b.get("total")
        before_energy = en_b.get("score")
        improvements = []
        if isinstance(instr_delta, int) and instr_delta < 0 and before_instr:
            improvements.append(
                f"动态指令减少 {abs(instr_delta):,} 条（{abs(instr_delta) / before_instr * 100:.1f}%）"
            )
        if isinstance(energy_delta, (int, float)) and energy_delta < 0 and before_energy:
            improvements.append(
                f"能耗估算分减少 {abs(energy_delta):,}（{abs(energy_delta) / before_energy * 100:.1f}%）"
            )
        concl = (
            f"优化通过验证闭环全部检查（编译成功、输出一致、指标改善），已被采纳。"
            + ("".join(f"{item}。" for item in improvements) if improvements else "")
        )
    elif state == "REJECTED":
        concl = (
            f"优化被拒绝并{( '已回滚' if verdict.get('rollback_done') else '未应用' )}："
            f"{verdict.get('failure_reason') or '原因未记录'}"
        )
    elif verdict is None:
        tot_b, tot_a = dyn_b.get("total"), dyn_a.get("total")
        if isinstance(tot_b, int) and isinstance(tot_a, int) and tot_b:
            concl = f"配置变更后动态指令数为优化前的 {tot_a / tot_b:.1%}（数据见上表，全部来自实测）。"
        else:
            concl = "前后指标均来自实测，完整结论需结合验证闭环。"
    else:
        concl = f"验证状态：{state}。"
    lines.append(concl)
    if notes:
        lines += ["", notes]
    lines.append("")

    # ---- 声明与来源注脚 ----
    lines += [ENERGY_DISCLAIMER, "", TCG_BOUNDARY, ""]
    if sources:
        lines += ["## 数据来源注脚", ""]
        for name, path in sources.items():
            lines.append(f"- {name}：`{path}`")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 MetricsReport/verdict JSON 生成对比报告")
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("verdict", type=Path, nargs="?")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ns = ap.parse_args(argv)

    before = json.loads(ns.before.read_text(encoding="utf-8"))
    after = json.loads(ns.after.read_text(encoding="utf-8"))
    verdict = json.loads(ns.verdict.read_text(encoding="utf-8")) if ns.verdict else None
    md = render_comparison_report(
        before, after, verdict,
        sources={"优化前 metrics": ns.before, "优化后 metrics": ns.after,
                 **({"verdict": ns.verdict} if ns.verdict else {})},
    )
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(md, encoding="utf-8")
    print(f"report written: {ns.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
