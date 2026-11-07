"""FR4：LLM 优化建议模块（docs/03 §5.8，docs/05 P6，M5）。

铁律（docs/06 §1 数据溯源）：
- LLM 只产出"建议 + 完整候选源码"，**绝不产出任何数字**；上下文中的指标
  全部来自实测 JSON，提示词明令禁止模型自行估算或编造数字；
- 输出必须是 JSON 数组，逐条 jsonschema 校验，不合法即丢弃并记录原因；
- LLM 不直接写文件，候选源码原样返回，由调用方交给 verifier 验证；
- 未配置 API / 网络失败 / 全部建议被拒时**降级**：返回空列表 + 降级原因，
  绝不抛业务异常打断流水线（界面据 degraded_reason 显示"规则引擎模式"）。

接口约定：DeepSeek Chat Completions 接口。base_url / api_key / model 三项由
用户在界面「优化建议」面板填写，仅存于会话内存，不落盘、不进仓库。
传输层可注入（_post_chat 参数），离线单测不打真网。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_S = 120.0
DEFAULT_TEMPERATURE = 0.2
#: 反汇编摘录带给 LLM 的最大行数（控制上下文体积，只摘热点函数）
MAX_DISASM_LINES = 60
#: 单条候选源码的最大长度（字符）——超长视为异常输出
MAX_CANDIDATE_CHARS = 200_000
#: 单条建议文本字段的最大长度
MAX_FIELD_CHARS = 2_000


@dataclass
class LlmConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = DEFAULT_TEMPERATURE
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass
class SuggestOutcome:
    """suggest() 的完整结果：建议列表 + 降级/丢弃诊断（可解释性要求）。"""

    suggestions: list[dict] = field(default_factory=list)
    degraded_reason: str = ""       # 非空 = 本次未调用 LLM 或整体失败（界面显示模式）
    rejected: list[dict] = field(default_factory=list)  # [{"index", "reason", "raw"}]


# ---------------------------------------------------------------------------
# 建议的 JSON Schema（docs/03 §6 Suggestion；验证失败即丢弃）
# ---------------------------------------------------------------------------

SUGGESTION_SCHEMA: dict = {
    "type": "object",
    "required": ["title", "modified_source", "expected_gain", "risk", "confidence"],
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 2, "maxLength": MAX_FIELD_CHARS},
        "location": {
            "type": "string", "maxLength": MAX_FIELD_CHARS,
            "description": "修改涉及的函数/行号描述（自由文本）",
        },
        "explanation": {"type": "string", "maxLength": MAX_FIELD_CHARS},
        "modified_source": {
            "type": "string", "minLength": 1, "maxLength": MAX_CANDIDATE_CHARS,
        },
        "expected_gain": {"enum": ["low", "medium", "high"]},
        "risk": {"enum": ["low", "medium", "high"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

_SUGGESTION_LIST_SCHEMA: dict = {
    "type": "array", "items": SUGGESTION_SCHEMA, "maxItems": 10,
}

_VALIDATOR = Draft202012Validator(_SUGGESTION_LIST_SCHEMA)

#: 数字禁令：候选源码中不得出现的"编造性能数字"句式（提示词 + 出口双重防线）
_CLAIM_RE = re.compile(
    r"(指令.{0,6}(减少|下降|降低)|快|提速|性能提升|能耗.{0,4}(降|减))[^。%\n]{0,30}\d",
)


# ---------------------------------------------------------------------------
# 上下文构造（全部喂实测数据；截断策略集中在此，便于调优）
# ---------------------------------------------------------------------------

def _disasm_excerpt(metrics: dict, static, max_lines: int = MAX_DISASM_LINES) -> str:
    """按静态指令数取最大的用户函数，摘其前 max_lines 行反汇编。"""
    funcs = (metrics.get("static") or {}).get("functions") or []
    if not funcs and static is not None:
        funcs = [
            {"name": f.name, "instructions": f.size_instructions}
            for f in getattr(static, "functions", [])
        ]
    users = [f for f in funcs if not str(f.get("name", "")).startswith("_")]
    if not users:
        return "（无函数级静态统计）"
    top = max(users, key=lambda f: f.get("instructions", 0))
    lines: list[str] = []
    for ins in getattr(static, "insns", []) or []:
        if ins.func == top["name"]:
            lines.append(ins.raw)
            if len(lines) >= max_lines:
                break
    head = f"热点函数 {top['name']}（静态 {top.get('instructions', 0)} 条指令，截取前 {len(lines)} 行）："
    return head + "\n" + "\n".join(lines) if lines else head + "（无指令明细）"


def _findings_excerpt(findings: list[dict], max_items: int = 8) -> str:
    if not findings:
        return "（规则引擎未发现确定性问题）"
    rows = []
    for f in findings[:max_items]:
        loc = f.get("location") or {}
        rows.append(
            f"- [{f.get('rule_id')}] {f.get('title')} @ {loc.get('function', '?')}"
            f"（建议：{f.get('suggestion', '')}；风险：{f.get('risk', '')}）"
        )
    return "规则引擎发现（证据均为实测）：\n" + "\n".join(rows)


def build_prompt(
    src_text: str,
    metrics: dict,
    findings: list[dict],
    disasm=None,
) -> list[dict]:
    """构造 chat messages：系统提示（纪律）+ 用户消息（实测上下文）。"""
    dynamic = metrics.get("dynamic") or {}
    metric_lines = [
        f"- arch={metrics.get('meta', {}).get('arch', '?')}, opt={metrics.get('meta', {}).get('opt', '?')}",
        f"- 动态指令总数（实测）: {dynamic.get('total', 0)}",
        f"- 类别分布（实测）: {json.dumps(dynamic.get('by_class', {}), ensure_ascii=False)}",
        f"- 函数调用次数（实测）: {dynamic.get('calls', 0)}",
        f"- 静态指令数（实测）: {(metrics.get('static') or {}).get('total_instructions', 0)}",
    ]
    system = (
        "你是 RISC-V 嵌入式 C 代码优化专家。你只会收到一个任务：阅读用户给出的"
        " C 源码与实测性能上下文，输出优化建议 JSON 数组。\n"
        "纪律：\n"
        "1. 上下文中标注（实测）的数字是测量结果，你不得自行估算、编造或外推任何"
        "性能数字；建议文本中禁止出现'指令减少 N 条''快 X%'之类的量化声明。\n"
        "2. 只输出 JSON 数组，不要 Markdown 代码围栏，不要解释性文字。每个元素："
        '{"title": 建议标题, "location": 位置描述, "explanation": 修改理由（可含'
        "对给定实测数据的引用）, \"modified_source\": 修改后的完整源码, "
        "\"expected_gain\": \"low|medium|high\", \"risk\": \"low|medium|high\", "
        "\"confidence\": 0~1}。\n"
        "3. modified_source 必须是完整可编译的单文件 C 源码（bare-metal freestanding，"
        "只用 stdio.h/stdlib.h/string.h/stdint.h，printf 输出不变）。\n"
        "4. 保持程序行为完全不变：输出内容逐字符一致。优化手段限于：循环不变量外提、"
        "强度削弱、查表、展开、消除冗余访存/转换等源码级改写。\n"
        "5. 最多 3 条建议；没有把握就不输出。"
    )
    user = (
        "## 源码\n```c\n" + src_text + "\n```\n\n"
        "## 实测性能上下文（RISC-V 裸机，QEMU 逐条指令统计）\n"
        + "\n".join(metric_lines) + "\n\n"
        + "## " + _disasm_excerpt(metrics, disasm) + "\n\n"
        + "## " + _findings_excerpt(findings) + "\n\n"
        + "请输出优化建议 JSON 数组。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 传输与解析
# ---------------------------------------------------------------------------

def _post_chat(cfg: LlmConfig, messages: list[dict]) -> str:
    """DeepSeek chat.completions 接口调用，返回助手文本。

    单独拆出以便测试注入 fake transport；失败抛 requests 异常（由
    suggest() 转成降级原因）。
    """
    import requests

    url = cfg.base_url + "/chat/completions"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        json={
            "model": cfg.model,
            "messages": messages,
            "temperature": cfg.temperature,
        },
        timeout=cfg.timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _extract_json_array(text: str):
    """从模型输出中提取 JSON 数组：容忍 ```json 围栏与前后杂文。"""
    cleaned = _FENCE_RE.sub("", text.strip())
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("输出中未找到 JSON 数组")
    return json.loads(cleaned[start : end + 1])


def _sanitize(item: dict) -> dict:
    """出口防线：截断超长字段、剔除候选源码中的编造量化声明句。"""
    out = dict(item)
    for key in ("title", "location", "explanation"):
        if isinstance(out.get(key), str) and len(out[key]) > MAX_FIELD_CHARS:
            out[key] = out[key][:MAX_FIELD_CHARS]
    src = out.get("modified_source", "")
    if isinstance(src, str) and _CLAIM_RE.search(src):
        # 注释里的编造数字声明一并清掉（正文代码不受影响）
        out["modified_source"] = _CLAIM_RE.sub("（性能声明由实测得出，此处不引用）", src)
    return out


def suggest(
    src: Path,
    metrics: dict,
    findings: list[dict],
    disasm=None,
    cfg: LlmConfig | None = None,
    transport=_post_chat,
) -> SuggestOutcome:
    """读取源码与实测上下文，调用 LLM 产出经过校验的建议列表。

    全链路不抛业务异常：任何失败都折叠为 degraded_reason（02 文档 NFR5
    降级可用；界面据此标注当前模式）。
    """
    outcome = SuggestOutcome()
    cfg = cfg or LlmConfig()
    if not (cfg.base_url.strip() and cfg.api_key.strip() and cfg.model.strip()):
        outcome.degraded_reason = "未填写 DeepSeek API Key（界面「优化建议」面板），当前为规则引擎模式"
        return outcome

    try:
        src_text = Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        outcome.degraded_reason = f"源码不可读: {exc}"
        return outcome

    messages = build_prompt(src_text, metrics, findings, disasm)
    try:
        raw = transport(cfg, messages)
    except Exception as exc:  # requests 异常/超时/HTTP 错误一律降级
        outcome.degraded_reason = f"LLM 请求失败: {type(exc).__name__}: {exc}"
        return outcome

    try:
        items = _extract_json_array(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        outcome.degraded_reason = f"LLM 输出不可解析: {exc}"
        outcome.rejected.append({"index": 0, "reason": str(exc), "raw": raw[:MAX_FIELD_CHARS]})
        return outcome

    errors = list(_VALIDATOR.iter_errors(items))
    if errors:
        # 整体 schema 不合法时逐条兜底：合法的单条保留
        kept: list[dict] = []
        single = Draft202012Validator(SUGGESTION_SCHEMA)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                outcome.rejected.append({"index": i, "reason": "元素不是对象", "raw": str(item)[:200]})
                continue
            errs = list(single.iter_errors(item))
            if errs:
                outcome.rejected.append(
                    {"index": i,
                     "reason": "; ".join(
                         f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                         for e in errs)[:300],
                     "raw": ""}
                )
            else:
                kept.append(_sanitize(item))
        if not kept:
            outcome.degraded_reason = "LLM 建议全部未通过 Schema 校验"
            return outcome
        outcome.suggestions = kept
        return outcome

    outcome.suggestions = [_sanitize(item) for item in items]
    return outcome
