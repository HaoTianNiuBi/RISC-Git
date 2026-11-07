"""RV-Doctor 网页界面（docs/03 §2 UI 层；FR1/FR3/FR6 展示侧）。

纪律：本文件只组装页面与调用 pipeline，不直接调用工具链；页面上出现的
每一个数字都来自 pipeline 落盘的 metrics.json（docs/06 §1 无证据不展示）。
科技风主题：.streamlit/config.toml 赛博蓝基底 + inject_tech_css() 注入的
电光蓝视觉层（含动效扫描线）——CSS 只改观感，不改任何数据与流程。
启动：.venv\\Scripts\\streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from optimizer.llm_agent import LlmConfig
from pipeline import CheckupResult, StepRecord, run_checkup, run_optimize  # noqa: E402

ARCHS = ["rv64gc", "rv64i", "rv32i"]
OPTS = ["O0", "O1", "O2", "O3", "Os"]

#: DeepSeek 接入默认值（用户可在界面覆盖；API Key 仅存会话内存，不落盘）
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"

#: 类别中文标签（饼图/表用；键与 metrics by_class 一致）
CLASS_LABELS = {
    "integer": "整数",
    "memory": "访存",
    "branch": "分支",
    "jump": "跳转",
    "muldiv": "乘除",
    "float": "浮点",
    "system": "系统",
}

#: 赛博蓝色板（与 CSS 主色一致：电光青蓝 → 电蓝 → 冰蓝高亮）
PALETTE = ["#38e5ff", "#4f8cff", "#2dd4bf", "#fbbf24", "#f87171", "#7df3ff", "#a78bfa"]

#: 流水线步骤名 → 中文（步骤轨迹时间线展示用；未映射的显示原名）
STEP_LABELS = {
    "receive": "接收源码",
    "compile": "交叉编译",
    "disasm": "反汇编",
    "run": "QEMU 运行",
    "parse": "轨迹解析",
    "aggregate": "指标聚合",
    "determinism": "确定性校验",
    "optimize": "优化建议",
    "verify": "验证闭环",
    "report": "生成报告",
}

#: 流水线状态 → 中文短标签（横幅展示用）
STATE_LABELS = {
    "DONE": "完成",
    "COMPILE_FAILED": "编译失败",
    "RUN_FAILED": "运行失败",
    "PARSE_FAILED": "解析失败",
    "NONDETERMINISTIC": "确定性校验未通过",
    "ERROR": "内部错误",
}

STATE_BANNER = {
    "DONE": ("success", "体检完成：以下所有数字均来自本机工具链实测。"),
    "COMPILE_FAILED": ("error", "编译失败 —— 详见下方错误信息与 build.log。"),
    "RUN_FAILED": ("error", "运行失败 —— 程序超时或异常退出，产物已留存。"),
    "PARSE_FAILED": ("error", "执行轨迹解析失败。"),
    "NONDETERMINISTIC": ("error", "确定性校验未通过：两次运行动态指标不一致（属异常，勿用于报告）。"),
    "ERROR": ("error", "内部错误（现场已保留在任务目录）。"),
}

st.set_page_config(
    page_title="RV-Doctor · RISC-V 性能体检",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# 科技风视觉层（纯 CSS，不含任何数据）
# ---------------------------------------------------------------------------

_TECH_CSS = """
<style>
/* ============ 赛博蓝主题：更亮的蓝底 + 电光青蓝 + 动效扫描线 ============ */
/* ---------- 全局：亮蓝底色 + 电光辉光 + 网格 + 全局扫描带 ---------- */
.stApp {
    background:
        radial-gradient(1200px 620px at 78% -12%, rgba(0,145,255,.22), transparent 62%),
        radial-gradient(1000px 520px at 6% 112%, rgba(0,217,255,.16), transparent 58%),
        linear-gradient(rgba(56,189,248,.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(56,189,248,.05) 1px, transparent 1px),
        #0a1a33;
    background-size: auto, auto, 44px 44px, 44px 44px, auto;
    color: #eaf6ff;
}
/* 全局扫描带：一道淡蓝光带周期性扫过整页（纯装饰，不拦截点击） */
.stApp::before {
    content: ""; position: fixed; left: 0; right: 0; top: 0; height: 200px;
    background: linear-gradient(180deg, transparent, rgba(0,217,255,.05), transparent);
    animation: rvd-scan-y 9s linear infinite;
    pointer-events: none; z-index: 2;
}
.block-container { padding-top: 1.1rem; max-width: 1420px; }
h1, h2, h3, h4 { font-family: 'Consolas', 'JetBrains Mono', ui-monospace, monospace !important;
                 letter-spacing: .4px; color: #eaf6ff !important; }
a { color: #38bdf8; }
hr { border-color: rgba(0,217,255,.2); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: #0c2144; }
::-webkit-scrollbar-thumb { background: linear-gradient(180deg, #0369a1, #1d4ed8); border-radius: 6px; }

@keyframes rvd-scan-y {          /* 纵向扫描带：从顶部扫到页面底部 */
    0%   { transform: translateY(-220px); }
    100% { transform: translateY(110vh); }
}
@keyframes rvd-sweep-x {         /* 横向扫光：横幅内往返 */
    0%   { left: -160px; }
    100% { left: 100%; }
}

/* ---------- 顶部横幅：渐变 + 往返扫光 + 扫描线纹理 ---------- */
.rvd-hero {
    position: relative; overflow: hidden;
    border: 1px solid rgba(0,217,255,.38); border-radius: 16px;
    background: linear-gradient(120deg, rgba(0,145,255,.30), rgba(79,124,255,.22) 55%, rgba(10,26,51,.55));
    box-shadow: 0 0 44px rgba(0,180,255,.20), inset 0 0 46px rgba(0,217,255,.07);
    padding: 22px 26px 18px; margin-bottom: 6px;
}
.rvd-hero::after {  /* 静态扫描线纹理 */
    content: ""; position: absolute; inset: 0; pointer-events: none;
    background: repeating-linear-gradient(0deg, rgba(234,246,255,.035) 0 1px, transparent 1px 4px);
}
.rvd-hero .rvd-sweep {  /* 动态扫光条 */
    position: absolute; top: 0; bottom: 0; width: 150px; pointer-events: none;
    background: linear-gradient(90deg, transparent, rgba(0,234,255,.20), transparent);
    animation: rvd-sweep-x 4.5s linear infinite;
}
.rvd-hero-badge {
    display: inline-block; font-family: 'Consolas', monospace; font-size: 11px;
    letter-spacing: 3px; color: #7df3ff; position: relative; z-index: 1;
    border: 1px solid rgba(0,217,255,.5); border-radius: 999px;
    padding: 3px 12px; margin-bottom: 8px; background: rgba(0,217,255,.10);
    text-shadow: 0 0 10px rgba(0,234,255,.5);
}
.rvd-hero h1 {
    margin: 0 0 6px; font-size: 2.0rem; line-height: 1.1; position: relative; z-index: 1;
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    background: linear-gradient(90deg, #f2fbff 25%, #38e5ff 60%, #6ea8ff);
    -webkit-background-clip: text; background-clip: text; color: transparent;
    text-shadow: 0 0 34px rgba(0,217,255,.35);
}
.rvd-hero h1 span { color: #38e5ff; -webkit-text-fill-color: #38e5ff; }
.rvd-hero small { font-size: .95rem; color: #9db8dd; font-weight: 400; margin-left: 10px; }
.rvd-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; position: relative; z-index: 1; }
.rvd-chips span {
    font-family: 'Consolas', monospace; font-size: 12px; color: #aef1ff;
    border: 1px solid rgba(0,217,255,.35); background: rgba(0,180,255,.10);
    border-radius: 8px; padding: 3px 10px;
}

/* ---------- 指标卡片：电光蓝描边 + 顶部光带 ---------- */
[data-testid="stMetric"] {
    position: relative; overflow: hidden;
    background: linear-gradient(160deg, rgba(0,180,255,.14), rgba(29,78,216,.10) 60%, rgba(13,32,63,.65));
    border: 1px solid rgba(0,217,255,.32); border-radius: 14px;
    padding: 16px 18px 12px;
    box-shadow: inset 0 0 28px rgba(0,217,255,.08), 0 6px 22px rgba(2,8,26,.4);
    transition: transform .18s ease, box-shadow .18s ease;
}
[data-testid="stMetric"]:hover { transform: translateY(-2px);
    box-shadow: inset 0 0 28px rgba(0,217,255,.13), 0 8px 32px rgba(0,180,255,.26); }
[data-testid="stMetric"]::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, #38e5ff, #4f8cff, transparent);
}
[data-testid="stMetricValue"] {
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    color: #f2fbff; text-shadow: 0 0 18px rgba(0,234,255,.45);
}
[data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] div {
    color: #9db8dd !important; font-size: .86rem;
}

/* ---------- 侧栏：渐变指挥舱面板 + 纵向扫描光 ---------- */
[data-testid="stSidebar"] {
    position: relative; overflow: hidden;
    background: linear-gradient(180deg, rgba(0,145,255,.20) 0%, rgba(12,33,68,.92) 34%, #0a1a33 100%);
    border-right: 1px solid rgba(0,217,255,.34);
    box-shadow: inset -20px 0 46px rgba(0,145,255,.08);
}
[data-testid="stSidebar"]::before {  /* 侧栏内扫描光带 */
    content: ""; position: absolute; left: 0; right: 0; top: 0; height: 120px;
    background: linear-gradient(180deg, transparent, rgba(0,217,255,.07), transparent);
    animation: rvd-scan-y 7s linear infinite;
    pointer-events: none; z-index: 0;
}
[data-testid="stSidebar"] > div { position: relative; z-index: 1; }
[data-testid="stSidebar"] .rvd-logo {
    font-family: 'Consolas', monospace; text-align: center; padding: 12px 6px;
    border: 1px solid rgba(0,217,255,.32); border-radius: 12px;
    background: linear-gradient(135deg, rgba(0,145,255,.20), rgba(29,78,216,.14));
    box-shadow: inset 0 0 24px rgba(0,217,255,.08);
    margin-bottom: 10px;
}
[data-testid="stSidebar"] .rvd-logo b {
    font-size: 1.25rem; letter-spacing: 1px;
    background: linear-gradient(90deg, #38e5ff, #6ea8ff);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}
[data-testid="stSidebar"] .rvd-logo i { display: block; font-style: normal;
    font-size: 10px; letter-spacing: 2.5px; color: #7df3ff; margin-top: 2px; }

/* ---------- 按钮：电光蓝能量渐变 ---------- */
.stButton > button {
    border-radius: 12px; font-weight: 700; letter-spacing: 1px;
    border: 1px solid rgba(0,217,255,.45);
}
.stButton > button[kind="primary"], [data-testid="baseButton-primary"] {
    background: linear-gradient(90deg, #0091ff, #2563eb) !important;
    color: #f2fbff !important; border: none !important;
    box-shadow: 0 0 30px rgba(0,180,255,.42), inset 0 0 14px rgba(255,255,255,.08);
    transition: transform .15s ease, box-shadow .15s ease;
}
.stButton > button[kind="primary"]:hover, [data-testid="baseButton-primary"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 0 44px rgba(0,217,255,.6), inset 0 0 14px rgba(255,255,255,.12);
}

/* ---------- 代码/输出：终端面板 ---------- */
div[data-testid="stCodeBlock"], .stCodeBlock {
    background: #061224 !important;
    border: 1px solid rgba(0,217,255,.3); border-radius: 12px;
    box-shadow: inset 0 0 36px rgba(0,217,255,.06), 0 4px 18px rgba(2,8,26,.4);
}
pre, code, [data-testid="stCodeBlock"] code {
    font-family: 'Consolas', 'JetBrains Mono', ui-monospace, monospace !important;
}

/* ---------- 页签：悬浮控制台 ---------- */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px; background: #0c2144; padding: 6px;
    border: 1px solid rgba(0,217,255,.22); border-radius: 12px;
}
.stTabs [data-baseweb="tab"] { border-radius: 8px; color: #9db8dd; }
.stTabs [data-baseweb="tab"]:hover { color: #aef1ff; background: rgba(0,180,255,.08); }
.stTabs [aria-selected="true"] {
    background: linear-gradient(90deg, rgba(0,180,255,.22), rgba(29,78,216,.22)) !important;
    color: #f2fbff !important; box-shadow: inset 0 0 0 1px rgba(0,217,255,.5);
}

/* ---------- 表格 / 提示条 / 步骤时间线 ---------- */
[data-testid="stDataFrame"] {
    border: 1px solid rgba(0,217,255,.24); border-radius: 12px; overflow: hidden;
}
[data-testid="stAlert"] {
    border-radius: 10px; border-left: 3px solid #38e5ff;
    background: rgba(12,33,68,.78); backdrop-filter: blur(4px);
}
.rvd-steps { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0 2px; }
.rvd-step {
    display: inline-flex; align-items: center; gap: 7px;
    font-family: 'Consolas', monospace; font-size: 12.5px; color: #d7e9ff;
    background: rgba(12,33,68,.88); border: 1px solid rgba(0,217,255,.26);
    border-radius: 999px; padding: 5px 13px;
}
.rvd-step i { width: 8px; height: 8px; border-radius: 50%;
    box-shadow: 0 0 8px currentColor; background: currentColor; }
.rvd-step b { color: #7df3ff; font-weight: 600; }
.rvd-card {
    border: 1px solid rgba(0,217,255,.24); border-radius: 14px;
    background: rgba(12,33,68,.6); padding: 14px 18px; margin-bottom: 4px;
}
.rvd-section {
    font-family: 'Consolas', monospace; font-size: .95rem; letter-spacing: 1px;
    color: #7df3ff; display: flex; align-items: center; gap: 8px; margin: 2px 0 8px;
}
.rvd-section::before { content: "▸"; color: #4f8cff; }

/* ---------- 上传控件内置英文汉化（font-size:0 隐藏原文，伪元素注入中文） ---------- */
[data-testid="stFileUploaderDropzone"] button p { font-size: 0; }
[data-testid="stFileUploaderDropzone"] button p::before {
    content: "选择文件"; font-size: 14px; color: inherit;
}
[data-testid="stFileUploaderDropzoneInstructions"] span { font-size: 0; }
[data-testid="stFileUploaderDropzoneInstructions"] span::before {
    content: "拖拽 .c 文件到此处，或点击上方按钮 · 每个文件上限 200MB";
    font-size: 13px; color: #9db8dd;
}

/* 无障碍：系统偏好减少动效时关闭所有扫描动画 */
@media (prefers-reduced-motion: reduce) {
    .stApp::before, .rvd-hero .rvd-sweep, [data-testid="stSidebar"]::before {
        animation: none; display: none;
    }
}
</style>
"""


def inject_tech_css() -> None:
    st.markdown(_TECH_CSS, unsafe_allow_html=True)


def render_hero() -> None:
    """科技感标题横幅（纯装饰，不含指标数据）。"""
    st.markdown(
        """
        <div class="rvd-hero">
          <div class="rvd-sweep"></div>
          <div class="rvd-hero-badge">RISC-V · 人工智能性能诊断实验室</div>
          <h1>RV<span>-</span>Doctor<small>RISC-V 程序性能体检台</small></h1>
          <div class="rvd-chips">
            <span>⚙ xPack GCC 15.2.0</span>
            <span>🖥 QEMU 11.1.0 · 逐条指令轨迹</span>
            <span>📏 指令级实测</span>
            <span>🔁 验证闭环 · 失败回滚</span>
            <span>🧾 证据链可复核</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_steps(steps) -> None:
    """流水线步骤时间线（发光状态点 + 耗时），步骤名汉化。"""
    chips = []
    for s in steps:
        color = "#2dd4bf" if s.ok else "#f87171"
        mark = "✔" if s.ok else "✘"
        label = STEP_LABELS.get(s.name, s.name)
        chips.append(
            f'<span class="rvd-step"><i style="color:{color}"></i>'
            f"{mark} {label}<b>{s.duration_s:.2f}s</b></span>"
        )
    st.markdown(f'<div class="rvd-steps">{"".join(chips)}</div>', unsafe_allow_html=True)


def section(title: str) -> None:
    st.markdown(f'<div class="rvd-section">{title}</div>', unsafe_allow_html=True)


def render_metrics(m: dict) -> None:
    """渲染 MetricsReport：静态/动态/运行/能耗四块，全部取自落盘 JSON。"""
    meta, static, dynamic = m["meta"], m["static"], m["dynamic"]
    run, energy = m["run"], m["energy"]

    section("指标总览")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("动态指令总数", f"{dynamic['total']:,}", help="一次运行实际执行的指令条数（确定性主指标）")
    c2.metric("静态指令数", f"{static['total_instructions']:,}", help="反汇编代码段指令条数")
    c3.metric("函数调用次数", f"{dynamic['calls']:,}", help="jal/jalr（写 ra）动态执行次数")
    c4.metric("能耗估算分", f"{energy['score']:,}", help="Σ(类别动态计数 × 权重)；相对估算，仅用于趋势对比")
    st.caption(f"⚠️ 能耗分免责声明：{energy['disclaimer']}")

    left, right = st.columns([2, 3])

    with left:
        section("动态类别分布")
        rows = [
            {"类别": CLASS_LABELS.get(k, k), "条数": v}
            for k, v in dynamic["by_class"].items()
            if v > 0
        ]
        if rows:
            df = pd.DataFrame(rows)
            fig = px.pie(df, names="类别", values="条数", hole=0.55,
                         color_discrete_sequence=PALETTE)
            fig.update_traces(textinfo="percent", textfont_color="#e6edf7")
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=10, b=0), height=330,
                legend=dict(font=dict(family="Consolas", size=12, color="#cfe3f5")),
            )
            st.plotly_chart(fig, width='stretch')
            st.dataframe(df, hide_index=True, width='stretch')
        else:
            st.info("无动态数据")

    with right:
        section("静态统计")
        st.markdown(
            f'<div class="rvd-card" style="font-size:.9rem;color:#cfe3f5">'
            f"代码段 <b style=\"color:#67e8f9\">{static['text_bytes']:,}</b> 字节 · "
            f"工具链 <b style=\"color:#67e8f9\">{meta.get('toolchain', '?')}</b><br>"
            f"QEMU <b style=\"color:#67e8f9\">{meta.get('qemu', '?')}</b></div>",
            unsafe_allow_html=True,
        )
        fns = static.get("functions") or []
        if fns:
            df = pd.DataFrame(fns).rename(columns={
                "name": "函数", "instructions": "指令数", "section": "段",
            })
            st.dataframe(df.head(12), hide_index=True, width='stretch')
            if len(df) > 12:
                st.caption(f"… 共 {len(df)} 个函数，完整列表见 disasm.txt")

        section("运行信息")
        st.markdown(
            f'<div class="rvd-card" style="font-size:.9rem;color:#cfe3f5">'
            f"退出码 <b style=\"color:{'#34d399' if run['exit_code'] == 0 else '#f87171'}\">"
            f"{run['exit_code']}</b> · "
            f"输出指纹 SHA256 <code>{run['stdout_sha256'][:16]}…</code> · "
            + (
                f"参考耗时 <b style=\"color:#67e8f9\">{run['wall_time_median_s']:.2f} s</b>"
                f"<span style=\"color:#8ea0bf\">（TCG 墙钟受宿主负载影响，仅作参考）</span>"
                if run.get("wall_time_median_s")
                else "参考耗时 n/a"
            )
            + "</div>",
            unsafe_allow_html=True,
        )
        diag = meta.get("diagnostics") or {}
        notes = []
        if diag.get("boot_rom_instructions"):
            notes.append(f"已剔除 QEMU 引导 ROM 指令 {diag['boot_rom_instructions']} 条（与程序无关）")
        if diag.get("trace_truncated") or diag.get("stdout_truncated"):
            notes.append("输出/日志超上限被截断")
        if diag.get("unclassified_mnemonics"):
            notes.append(f"未归类助记符：{', '.join(diag['unclassified_mnemonics'][:5])}")
        if notes:
            st.caption("诊断：" + "；".join(notes))


def render_result(r: CheckupResult) -> None:
    kind, msg = STATE_BANNER.get(r.state, ("error", r.state))
    state_label = STATE_LABELS.get(r.state, r.state)
    getattr(st, kind)(f"**{state_label}** — {msg}")
    st.markdown(
        f'<div class="rvd-steps">'
        f'<span class="rvd-step">任务 <b>{r.task_id}</b></span>'
        f'<span class="rvd-step">架构 <b>{r.arch}</b></span>'
        f'<span class="rvd-step">优化 <b>{r.opt}</b></span>'
        f'<span class="rvd-step">产物 <b>{r.workdir}</b></span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["🖥 程序输出", "📊 指标", "🧭 步骤轨迹", "🧪 错误详情"])
    with tabs[0]:
        st.code(r.stdout if r.stdout else "(空)", language=None)
    with tabs[1]:
        if r.metrics:
            render_metrics(r.metrics)
        else:
            st.info("指标不可用（流程未走到聚合步骤）")
    with tabs[2]:
        if r.steps:
            render_steps(r.steps)
        else:
            st.caption("（本次为会话恢复视图，步骤轨迹见任务目录）")
    with tabs[3]:
        if r.error:
            st.code(r.error, language=None)
        else:
            st.success("无错误")


def render_optimize_section(summary: dict) -> None:
    """优化建议阶段入口：DeepSeek 配置（界面自填）→ 规则取证 → AI 建议 → 验证闭环。"""
    section("优化建议（规则 + AI）")

    # ---- DeepSeek 接入配置：用户在界面填写，仅存会话内存（不落盘、不进仓库）
    st.session_state.setdefault("ds_base_url", DEEPSEEK_BASE_URL)
    st.session_state.setdefault("ds_model", DEEPSEEK_MODEL)
    st.session_state.setdefault("ds_api_key", "")
    with st.expander("🔑 DeepSeek 接入配置", expanded=not st.session_state["ds_api_key"]):
        st.text_input("Base URL", key="ds_base_url",
                      help="DeepSeek 接口地址，保持默认即可")
        st.text_input("模型名", key="ds_model",
                      help="deepseek-flash / deepseek-v4-pro（以平台实测可用为准）")
        st.text_input("API Key", key="ds_api_key", type="password",
                      help="在 platform.deepseek.com 获取；仅保存在当前浏览器会话内存，"
                           "不写入任何文件、不进仓库，刷新页面后需重新填写")
    cfg = LlmConfig(
        base_url=st.session_state["ds_base_url"].strip(),
        api_key=st.session_state["ds_api_key"].strip(),
        model=st.session_state["ds_model"].strip(),
    )
    if not (cfg.base_url and cfg.api_key and cfg.model):
        st.warning("未填写 DeepSeek API Key：点击下方按钮仅执行规则取证（无 AI 建议）。")

    if st.button(
        "🧠 生成优化建议并验证", type="primary", width='stretch',
        help="对最近一次体检的任务目录做规则取证与 AI 建议，每个候选修改"
             "都要走完“编译 → 输出比对 → 确定性双跑 → 指标对比”才被采纳",
    ):
        with st.spinner("规则取证 → AI 建议 → 逐候选验证（每个候选约 10~30 秒）…"):
            res = run_optimize(summary["workdir"], llm_cfg=cfg)
        st.session_state["last_optimize"] = res.summary()
    if "last_optimize" not in st.session_state:
        st.caption(
            "对体检完成的任务做只读分析：规则引擎 R1~R9 取证 + AI 修改建议"
            "（未配置 LLM 时自动降级为纯规则模式）。所有候选经验证闭环后才"
            "采纳，失败自动回滚并保留证据。"
        )
        return
    render_optimize(st.session_state["last_optimize"])


def render_optimize(o: dict) -> None:
    """渲染 run_optimize.summary()：模式横幅、步骤、发现、建议与验证结论。"""
    if o.get("state") == "ERROR":
        st.error(f"**优化阶段失败** — {o.get('error')}")
        return

    if o.get("llm_mode") == "llm":
        st.success(
            "**AI 建议模式**（DeepSeek）：AI 只产出候选代码，"
            "是否采纳由验证闭环实测决定。"
        )
    else:
        st.warning(
            f"**规则引擎模式**（本次未启用 AI 建议）：{o.get('llm_degraded_reason') or 'LLM 未配置或未给出候选'}"
        )
    if o.get("steps"):
        steps = [StepRecord(**{k: v for k, v in s.items()
                               if k in StepRecord.__dataclass_fields__})
                 for s in o["steps"]]
        render_steps(steps)

    # ---- 规则取证（证据均来自实测反汇编/计数）
    findings = o.get("findings") or []
    with st.expander(
        f"🧭 规则取证：{len(findings)} 条发现（R1~R9，证据不足的规则按纪律静默）",
        expanded=bool(findings),
    ):
        if not findings:
            st.info("规则引擎未发现确定性问题")
        else:
            rows = [{
                "规则": f.get("rule_id"),
                "问题": f.get("title"),
                "位置": (f.get("location") or {}).get("function", "?"),
                "建议": f.get("suggestion"),
                "风险": f.get("risk"),
                "置信度": f.get("confidence"),
            } for f in findings]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
            for f in findings:
                with st.expander(f"[{f.get('rule_id')}] {f.get('title')} — 证据"):
                    st.json(f.get("evidence") or {})

    # ---- 候选修改与验证结论
    suggestions = o.get("suggestions") or []
    verdicts = o.get("verdicts") or []
    sug_map = {f"llm-{i + 1:02d}": s for i, s in enumerate(suggestions)}
    if not suggestions:
        st.info("本次没有可验证的候选修改（AI 未启用或未给出建议），规则发现见上方")
    else:
        section("候选修改与验证闭环")
        for v in verdicts:
            sid = v.get("suggestion_id", "")
            sug = sug_map.get(sid, {})
            accepted = v.get("state") == "ACCEPTED"
            head = sug.get("title") or sid
            icon = "✅" if accepted else "❌"
            star = "　★ 最优候选" if v.get("promoted") else ""
            st.markdown(f"{icon} **{head}**　`{sid}` · {v.get('state')}{star}")
            bits = []
            if sug:
                bits.append(f"预期收益 {sug.get('expected_gain', '?')}")
                bits.append(f"风险 {sug.get('risk', '?')}")
                conf = sug.get("confidence")
                bits.append(f"置信度 {conf:.0%}" if isinstance(conf, (int, float)) else "置信度 —")
            before = (v.get("before") or {}).get("dynamic.total")
            after = (v.get("after") or {}).get("dynamic.total")
            if before is not None and after is not None:
                bits.append(f"动态指令 {before:,} → {after:,}")
            st.caption(" · ".join(bits))
            if sug.get("explanation"):
                st.write(sug["explanation"])
            if not accepted and v.get("failure_reason"):
                st.caption(f"失败原因：{v['failure_reason']}")
            if sug.get("modified_source"):
                with st.expander(f"候选源码（{sid}）"):
                    st.code(sug["modified_source"], language="c")

        best_id = o.get("best")
        if best_id:
            bv = next((v for v in verdicts
                       if v.get("suggestion_id") == best_id and v.get("promoted")), None)
            if bv:
                cand_src = Path(bv["candidate_dir"]) / "candidate.c"
                if cand_src.is_file():
                    with st.expander(f"查看采纳后的最优候选源码（{best_id}，验证通过）"):
                        st.code(cand_src.read_text(encoding="utf-8", errors="replace"),
                                language="c")

    # ---- 对比报告
    reports = o.get("reports") or []
    if reports:
        section("优化前后对比报告")
        for rp in reports:
            p = Path(rp)
            if p.is_file():
                with st.expander(f"🧾 {p.name}"):
                    st.markdown(p.read_text(encoding="utf-8"))


def main() -> None:
    inject_tech_css()
    render_hero()

    # ---- 侧栏：编译配置 ----------------------------------------------------
    with st.sidebar:
        st.markdown(
            '<div class="rvd-logo"><b>RV-Doctor</b>'
            "<i>诊断控制台</i></div>",
            unsafe_allow_html=True,
        )
        st.header("编译配置")
        arch = st.selectbox("目标架构", ARCHS, index=0, help="rv64gc 含硬件浮点；rv64i/rv32i 浮点退化为软浮点库")
        opt = st.selectbox("优化等级", OPTS, index=0)
        det = st.checkbox(
            "确定性校验（运行两次比对）", value=True,
            help="同一 ELF 连续两次运行的动态指令数必须完全一致（docs/06 §5.1）",
        )
        st.divider()
        st.markdown(
            "**数据红线**\n"
            "- 指标先落盘（`workdir/<任务号>/metrics.json`）再渲染\n"
            "- 能耗分与耗时为估算/参考值，已标注\n"
            "- 每次任务独立目录，证据链可复核"
        )

    # ---- 输入：粘贴 / 上传 ---------------------------------------------------
    tab_paste, tab_upload = st.tabs(["📝 粘贴代码", "📤 上传 .c 文件"])
    source_text = ""
    uploaded = None
    with tab_paste:
        source_text = st.text_area(
            "C 源码", height=280,
            value="#include <stdio.h>\n\nint main(void) {\n    int sum = 0;\n"
                  "    for (int i = 1; i <= 100; i++) {\n        sum += i;\n    }\n"
                  "    printf(\"sum=%d\\n\", sum);\n    return 0;\n}\n",
        )
    with tab_upload:
        uploaded = st.file_uploader("上传单个 .c 文件（≤1 MB）", type=["c"])

    if st.button("🩺 一键体检", type="primary", width='stretch'):
        if uploaded is not None:
            src_bytes = uploaded.getvalue()
            if len(src_bytes) > (1 << 20):
                st.error("源码超过 1 MB 上限")
                return
            with st.spinner(f"编译并运行 {uploaded.name} …（约 10~30 秒）"):
                r = run_checkup(src_bytes.decode("utf-8", errors="replace"),
                                arch=arch, opt=opt, benchmark=uploaded.name,
                                verify_determinism=det)
        elif source_text.strip():
            with st.spinner("编译并运行 …（约 10~30 秒）"):
                r = run_checkup(source_text, arch=arch, opt=opt,
                                benchmark="pasted", verify_determinism=det)
        else:
            st.warning("请先粘贴代码或上传 .c 文件")
            return
        st.session_state["last_result"] = r.summary()

    # ---- 会话中的最近一次结果 ------------------------------------------------
    if "last_result" in st.session_state:
        st.divider()
        summary = st.session_state["last_result"]
        r = CheckupResult(
            task_id=summary["task_id"], state=summary["state"],
            steps=[StepRecord(**{k: v for k, v in s.items() if k in StepRecord.__dataclass_fields__})
                   for s in summary.get("steps", [])],
            workdir=Path(summary["workdir"]) if summary["workdir"] else None,
            metrics=summary["metrics"], stdout=summary["stdout"],
            error=summary["error"], arch=summary["arch"], opt=summary["opt"],
        )
        render_result(r)

        # ---- 优化阶段：体检完成后可继续生成建议并验证（docs/11 D1 接线）
        if r.state == "DONE" and summary.get("workdir"):
            st.divider()
            render_optimize_section(summary)


if __name__ == "__main__":
    main()
