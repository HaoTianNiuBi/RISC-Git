#!/usr/bin/env python3
"""监听 K1 终端状态机，逐轮记录现场交互（阶段 5.3 用）。

只读 /api/state，不触发任何操作；每一轮由参赛者在触屏上点击开始/停止。

输出 JSONL，每行一次状态跃迁：
    {"ts": "...", "phase": "recording", "message": "...", "intent": null, ...}

用法：
    python 板端验证/edge/watch_session.py --minutes 45 --out D:/.../session.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime

URL = "http://127.0.0.1:18793/api/state"
BUSY = {"preparing", "recording", "recognizing", "classifying", "running"}


def snapshot() -> dict | None:
    try:
        with urllib.request.urlopen(URL, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    deadline = time.time() + args.minutes * 60
    prev_phase = None
    started_busy_at = None
    round_no = 0

    with open(args.out, "a", encoding="utf-8") as out:
        out.write(json.dumps({"event": "watch_started",
                              "ts": datetime.now().isoformat(timespec="seconds")},
                             ensure_ascii=False) + "\n")
        out.flush()

        while time.time() < deadline:
            state = snapshot()
            if state is None:
                time.sleep(args.interval)
                continue

            phase = state.get("phase")
            if phase != prev_phase:
                now = time.time()
                if phase in BUSY and prev_phase not in BUSY:
                    started_busy_at = now
                record = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "phase": phase,
                    "message": state.get("message"),
                    "intent": state.get("intent"),
                    "transcript": state.get("transcript"),
                    "has_result": bool(state.get("result")),
                    "result": state.get("result") if isinstance(state.get("result"), dict) else None,
                }
                if phase in ("done", "error") and started_busy_at:
                    round_no += 1
                    record["round_no"] = round_no
                    record["busy_seconds"] = round(now - started_busy_at, 3)
                    started_busy_at = None
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                prev_phase = phase

            time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())