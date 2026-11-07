#!/usr/bin/env python3
"""录音增益上限的 A/B 对比（板端，离线）。

对同一段原始 WAV 用不同的增益上限各跑一遍完整前处理 + SenseVoice，
用它判断「识别不出来」是音量问题还是声学问题。

用法（板端）：
    ../asr/venv/bin/python gain_ab.py --raw capture/raw.wav --caps 24 40
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

WARMUP_SECONDS = 0.75
FILTER = ('highpass=f=110:p=2,highpass=f=110:p=2,volume=12dB,'
          'alimiter=limit=0.76:attack=2:release=60:level=0:latency=1')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--caps", nargs="+", type=float, default=[24.0, 40.0])
    ap.add_argument("--asr-dir", default="/home/haotian/rv-doctor-edge-validation/asr")
    ap.add_argument("--model-dir", default="/home/haotian/rv-doctor-edge-validation/asr/models/sensevoice")
    args = ap.parse_args()

    import numpy as np
    sys.path.insert(0, args.asr_dir)
    from prepare_audio import prepare  # noqa: E402

    import spacemit_asr as asr  # noqa: E402

    raw = Path(args.raw)
    with wave.open(str(raw), "rb") as w:
        rate, channels = w.getframerate(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").reshape(-1, channels)
    mono = samples[round(rate * WARMUP_SECONDS):].astype(float).mean(axis=1)
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    need = 20 * math.log10(25000 / max(peak, 1))

    config = asr.Config(model_dir=args.model_dir, backend="sensevoice")
    config.language = asr.Language.ZH
    config._config.extra_params = {"provider": "cpu"}
    config._config.num_threads = 4
    config._config.vad_enabled = False
    engine = asr.Engine(config)
    engine.initialize()

    out = {"raw": str(raw), "source_peak": peak, "needed_gain_db": round(need, 2), "trials": []}
    try:
        for cap in args.caps:
            gain = min(cap, need)
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                prepared = tmpdir / "prepared.wav"
                filtered = tmpdir / "filtered.wav"
                report = prepare(raw, prepared, target_rate=16000,
                                 trim_start=WARMUP_SECONDS, gain_db=gain)
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-n", "-i", str(prepared),
                     "-af", FILTER, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(filtered)],
                    check=True, timeout=30,
                )
                result = engine.recognize_file(str(filtered))
                out["trials"].append({
                    "cap_db": cap,
                    "applied_gain_db": round(gain, 2),
                    "prepared_peak": report["peak"],
                    "text": result.text,
                })
    finally:
        engine.shutdown()

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())