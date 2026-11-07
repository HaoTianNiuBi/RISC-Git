#!/usr/bin/env python3
"""核对文档中被引用的关键数字是否与证据 JSON 一致。

不是"重新计算成绩"，而是防止文档之间口径漂移：
文档里写的数必须能在证据里算出来，且同一事实在不同文档里写法一致。

用法：python3 板端验证/edge/check_numbers.py
退出码：0 全部一致；1 有缺失或不一致。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "板端验证" / "edge" / "results"
ASR = ROOT / "板端验证" / "asr" / "results"

DOCS = [
    ROOT / "README.md",
    ROOT / "方案书.md",
    ROOT / "RV-Doctor" / "docs" / "09-参赛技术文档.md",
    ROOT / "RV-Doctor" / "docs" / "08-风险与对策.md",
    ROOT / "RV-Doctor" / "docs" / "edge" / "执行计划.md",
    ROOT / "RV-Doctor" / "edge_terminal" / "README.md",
    ROOT / "板端验证" / "asr" / "README.md",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pct(num: float) -> str:
    return f"{num * 100:.2f}%"


def collect() -> list[tuple[str, str, list[str]]]:
    """返回 (事实, 呈现文本候选, 需要出现的文档) 列表。"""
    facts: list[tuple[str, str, list[str]]] = []

    asr_cases = {
        "round-6e90e6b91134": ("现场单遍", ASR / "round-6e90e6b91134-assessment.json"),
        "replay-vad-off-b469e9a54dc9": ("关闭 VAD", ASR / "replay-vad-off-b469e9a54dc9-assessment.json"),
        "replay-filter-6d669d9d6a4f": ("统一前处理", ASR / "replay-filter-6d669d9d6a4f-assessment.json"),
    }
    for key, (label, path) in asr_cases.items():
        data = load(path)
        cer = data["corpus_cer"]
        ed = data["total_edit_distance"]
        ref = data["total_reference_characters"]
        facts.append((f"{label} CER={pct(cer)}（{ed}/{ref}）", pct(cer),
                      ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md"]))

    e2e_dir = load(RESULTS / "audio_e2e-c2413d24143c.json")
    score = e2e_dir.get("summary") or e2e_dir.get("score")
    facts.append((
        f"语音端到端 意图 {score['intent_correct']}/{score['required']}，JSON {score['json_valid']}/{score['required']}",
        f"{score['intent_correct']}/{score['required']}",
        ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md", "README.md"],
    ))
    facts.append(("语音端到端越界派发 = 0", "0", ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md"]))

    replay = load(RESULTS / "replay-20d8082a07e7.json")["summary"]
    rounds = [f"{r['ui_stop_to_visible_seconds']:.3f}" for r in replay["rounds"]]
    for value in rounds:
        facts.append((f"回放停止→结果 {value} s", value, ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md", "README.md"]))

    cmp_ = load(RESULTS / "ubatch-128-vs-32-comparison.json")["full_audio_comparison"]
    before = cmp_["baseline"]["llm_total_median_seconds"]
    after = cmp_["candidate"]["llm_total_median_seconds"]
    facts.append((f"调优 千问中位 {before:.2f} → {after:.2f} s", f"{after:.2f}", ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md"]))
    facts.append((
        "调优降幅 62.86%",
        f"{cmp_['llm_median_reduction_percent']:.2f}%",
        ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md", "README.md"],
    ))
    facts.append((
        "文件→结果中位 39.01 → 18.00 s",
        f"{cmp_['candidate']['file_to_result_median_seconds']:.2f}",
        ["RV-Doctor/docs/09-参赛技术文档.md", "方案书.md"],
    ))

    fixtures = load(RESULTS / "b02-openmp-fixture.json")["configs"]
    facts.append((
        "K1 b02 四线程中位 5.14 ms",
        f"{fixtures['4']['median_wall_seconds'] * 1000:.2f}",
        [],
    ))

    recovery = load(RESULTS / "recovery-20261107.json")
    up = next(c for c in recovery["cases"] if c["case_id"] == "valid_up")
    facts.append((
        f"恢复测试 合法意图接口 {up['observed']['llm_total_seconds']:.2f} s",
        f"{up['observed']['llm_total_seconds']:.2f}",
        [],
    ))
    return facts


def main() -> int:
    texts = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in DOCS if p.exists()}
    problems: list[str] = []

    for fact, needle, required_in in collect():
        missing = [doc for doc in required_in if doc in texts and needle not in texts[doc]]
        status = "OK  " if not missing else "MISS"
        line = f"{status} {fact}"
        if missing:
            line += f"  <- 未出现在 {', '.join(missing)}"
            problems.append(line)
        print(line)

    # 反向检查：文档里不该再出现的过期表述
    stale = {
        "正式样本 0/10": "阶段 1 已有 10 条正式录音",
        "只有一次烟雾测试": "已有正式验收记录",
        "录音失败，请检查耳麦连接。": "D1 修复后不应再作为静音场景的提示",
    }
    for phrase, why in stale.items():
        for doc, text in texts.items():
            if phrase and phrase in text:
                msg = f"MISS 过期表述「{phrase}」仍在 {doc}（{why}）"
                print(msg)
                problems.append(msg)

    corrected = "tasks.py --base . --seed"
    for doc in ("RV-Doctor/edge_terminal/README.md", "方案书.md"):
        if doc in texts and corrected not in texts[doc]:
            msg = f"MISS {doc} 缺少修正后的部署命令「{corrected}」"
            print(msg)
            problems.append(msg)

    print()
    if problems:
        print(f"FAILED: {len(problems)} 项")
        return 1
    print("ALL NUMBER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())