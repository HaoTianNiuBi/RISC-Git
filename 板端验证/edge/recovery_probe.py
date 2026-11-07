#!/usr/bin/env python3
"""阶段 5.4 异常与恢复探针。

只读不写：不触发录音，不执行真实任务，仅用给定的文本走一遍
「意图判定 → 白名单 → 派发」这条链，并记录千问不可用时的行为。

用法（板端，terminal 目录内）：
    ../asr/venv/bin/python recovery_probe.py --base . --label valid_up --text 开始体检
    ../asr/venv/bin/python recovery_probe.py --base . --label unknown --text 帮我订一张明天的电影票

输出：单行 JSON，含意图、是否派发、耗时与异常信息。
判定要点：`dispatched` 为 false 表示没有任何任务被执行。
"""

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


class SpyDispatcher:
    """记录 dispatch 调用，但不真正执行任务。"""

    def __init__(self):
        self.calls = []

    def dispatch(self, intent):
        self.calls.append(intent)
        return {'ok': True, 'message': 'spy dispatch', 'intent': intent}


DEFAULT_TEXTS = {
    # 合法意图：期望派发 start_checkup
    'valid_up': '开始体检',
    'valid_down': '开始体检',
    'valid_recovery': '开始体检',
    # 越界意图：期望不派发
    'unknown': '帮我订一张明天的电影票',
    # 跨类别合法意图：用于确认白名单不是只看一类
    'compare': '比较四线程和八线程的运行结果',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--text', default=None, help='不给则按 label 取内置文本')
    args = parser.parse_args()
    if args.text is None:
        if args.label not in DEFAULT_TEXTS:
            parser.error(f'label {args.label!r} 没有内置文本，请显式给 --text')
        args.text = DEFAULT_TEXTS[args.label]

    base = args.base.resolve()
    sys.path.insert(0, str(base))

    import terminal as T  # noqa: E402  (需要 base 在 sys.path 上)
    import intent as I    # noqa: E402

    definitions = T.read_definitions(base)
    client = I.IntentClient(definitions['intent_output_schema'])
    spy = SpyDispatcher()

    record = {
        'label': args.label,
        'input_text': args.text,
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'llama_url': getattr(I, 'DEFAULT_URL', None),
    }

    started = time.perf_counter()
    try:
        outcome = T.route(args.text, client, spy)
        record['route_returned'] = True
        record['intent'] = outcome.get('intent')
        record['json_valid'] = outcome.get('json_valid')
        record['observed_dispatch_intent'] = outcome.get('observed_dispatch_intent')
        record['failure_reason'] = outcome.get('failure_reason')
        record['task_result'] = outcome.get('task_result')
        record['llm_total_seconds'] = outcome.get('total_latency_s')
        record['llm_first_content_seconds'] = outcome.get('first_content_latency_s')
    except Exception as exc:  # 千问不可用时预期走到这里
        record['route_returned'] = False
        record['exception_type'] = type(exc).__name__
        record['exception_message'] = str(exc)
        record['traceback_tail'] = traceback.format_exc().strip().splitlines()[-1]

    record['wall_seconds'] = time.perf_counter() - started
    record['dispatch_calls'] = spy.calls
    record['dispatched'] = bool(spy.calls)
    record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()

    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())