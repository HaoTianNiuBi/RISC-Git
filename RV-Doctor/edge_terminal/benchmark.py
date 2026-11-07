"""Isolated K1 replay of a frozen WAV through the normal terminal UI."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import threading
import time
import uuid
import wave

from audio import AudioPipeline, digest
from intent import IntentClient
from tasks import TaskDispatcher
from terminal import Terminal, atomic_json, handler_for, read_definitions


SAMPLE_SECONDS = 0.25


def _proc_kib(path: Path, key: str) -> int | None:
    try:
        with path.open(encoding="ascii", errors="replace") as stream:
            for line in stream:
                name, _, value = line.partition(":")
                if name == key:
                    fields = value.split()
                    if len(fields) >= 2 and fields[1] == "kB":
                        return int(fields[0])
    except (OSError, ValueError):
        pass
    return None


def _cgroup_memory_path(pid: int) -> Path | None:
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            mount = Path("/sys/fs/cgroup")
            path = (mount / line[3:].lstrip("/") / "memory.current").resolve()
            if path.is_relative_to(mount):
                return path
    return None


def _read_bytes_count(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


class MemorySampler:
    """Sample process RSS, cgroup-v2 usage and system available memory."""

    def __init__(self, directory: Path, llm_pid: int):
        self.directory = directory
        self.pids = {"terminal": os.getpid(), "llm": llm_pid}
        self.cgroups = {name: _cgroup_memory_path(pid) for name, pid in self.pids.items()}
        self.samples = []
        self.ended = threading.Event()
        self.started = time.perf_counter()
        self.thread = threading.Thread(target=self._sample_until_stopped, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _sample(self) -> None:
        timestamp = time.perf_counter()
        # Services in the same cgroup share the same memory.current observation.
        cgroup_values = {path: _read_bytes_count(path) for path in set(self.cgroups.values())}
        self.samples.append({
            "elapsed_seconds": timestamp - self.started,
            "cgroup_memory_current_bytes": {
                name: cgroup_values[path] for name, path in self.cgroups.items()
            },
            "process_vmrss_kib": {
                name: _proc_kib(Path(f"/proc/{pid}/status"), "VmRSS")
                for name, pid in self.pids.items()
            },
            "system_mem_available_kib": _proc_kib(Path("/proc/meminfo"), "MemAvailable"),
        })

    def _sample_until_stopped(self) -> None:
        self._sample()
        while not self.ended.wait(SAMPLE_SECONDS):
            self._sample()

    def finish(self) -> None:
        self.ended.set()
        self.thread.join()
        self._sample()
        atomic_json(self.directory / "memory.json", {
            "start_event": "replay_worker_observed_stop_event",
            "end_event": "terminal_worker_returned",
            "requested_interval_seconds": SAMPLE_SECONDS,
            "units": {
                "cgroup_memory_current_bytes": "bytes",
                "process_vmrss_kib": "KiB",
                "system_mem_available_kib": "KiB",
            },
            "pids": self.pids,
            "cgroup_memory_current_paths": {
                name: str(path) if path is not None else None
                for name, path in self.cgroups.items()
            },
            "samples": self.samples,
        })


class ReplayCapture:
    def __init__(self, source: Path, frozen_sha256: str, llm_pid: int):
        self.source = source
        self.frozen_sha256 = frozen_sha256
        self.llm_pid = llm_pid
        self.shutting_down = threading.Event()
        self.sampler = None

    def __call__(self, path: Path, stop: threading.Event, ready) -> None:
        shutil.copyfile(self.source, path)
        if digest(path) != self.frozen_sha256:
            raise ValueError("回放音频的 SHA256 与冻结值不符。")
        ready()
        stop.wait()
        if self.shutting_down.is_set():
            raise ValueError("回放服务正在停止。")
        self.sampler = MemorySampler(path.parent, self.llm_pid)
        self.sampler.start()

    def finish(self) -> None:
        sampler, self.sampler = self.sampler, None
        if sampler is not None:
            sampler.finish()


class ReplayTerminal(Terminal):
    def __init__(self, base, audio, client, dispatcher, definitions, replay):
        self.replay = replay
        super().__init__(base, audio, client, dispatcher, definitions, capture_audio=replay)

    def start(self, case_id):
        if case_id is not None:
            raise ValueError("固定音频回放请使用普通体验入口。")
        return super().start(None)

    def _run(self, case_id):
        try:
            return super()._run(case_id)
        finally:
            self.replay.finish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--llm-pid", type=int, required=True)
    parser.add_argument("--port", type=int, default=18794)
    args = parser.parse_args(argv)

    expected = args.sha256.lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        parser.error("--sha256 must contain 64 hexadecimal digits")
    if args.llm_pid <= 0 or not Path(f"/proc/{args.llm_pid}/status").is_file():
        parser.error("--llm-pid must identify a running process")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    base = args.base.resolve(strict=True)
    model_dir = args.model_dir.resolve(strict=True)
    source = args.source.resolve(strict=True)
    if digest(source) != expected:
        parser.error("--source does not match the frozen SHA256")
    with wave.open(str(source), "rb") as recording:
        audio_format = {
            "sample_rate_hz": recording.getframerate(),
            "channels": recording.getnchannels(),
            "sample_width_bytes": recording.getsampwidth(),
            "frames": recording.getnframes(),
        }
    if (audio_format["sample_rate_hz"], audio_format["channels"],
            audio_format["sample_width_bytes"]) != (48000, 2, 2):
        parser.error("--source must be a 48 kHz stereo PCM16 WAV")

    definitions = read_definitions(base)
    page = (base / "terminal.html").read_bytes()
    client = IntentClient(definitions["intent_output_schema"])
    run_dir = base / "measurements" / ("replay-" + uuid.uuid4().hex[:12])
    run_dir.mkdir(parents=True)
    for name in ("b02_matrix_mul.c", "b02_matrix_mul.out"):
        shutil.copyfile(base / name, run_dir / name)

    started = time.perf_counter()
    audio = AudioPipeline(model_dir)
    initialization_wall = time.perf_counter() - started
    replay = ReplayCapture(source, expected, args.llm_pid)
    app = ReplayTerminal(run_dir, audio, client, TaskDispatcher(run_dir), definitions, replay)
    atomic_json(run_dir / "benchmark.json", {
        "run_id": run_dir.name,
        "mode": "fixed_raw_wav_replay_through_terminal_ui",
        "created_at_board_utc": datetime.now(timezone.utc).isoformat(),
        "command": [sys.executable, str(Path(__file__).resolve()),
                    *(sys.argv[1:] if argv is None else argv)],
        "source_path": str(source),
        "source_sha256": expected,
        "source_audio_format": audio_format,
        "definitions_sha256": digest(base / "acceptance-cases.json"),
        "terminal_html_sha256": digest(base / "terminal.html"),
        "benchmark_source_sha256": digest(run_dir / "b02_matrix_mul.c"),
        "benchmark_golden_sha256": digest(run_dir / "b02_matrix_mul.out"),
        "model_dir": str(model_dir),
        "asr_pipeline_init_wall_seconds": initialization_wall,
        "asr": audio.metadata,
        "llm_request": client.metadata,
        "llm_pid": args.llm_pid,
        "terminal_pid": os.getpid(),
        "requested_memory_sample_interval_seconds": SAMPLE_SECONDS,
    })

    def stop_service(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_service)
    try:
        with ThreadingHTTPServer(
            ("127.0.0.1", args.port), handler_for(app, page),
        ) as server:
            print(f"BENCHMARK_READY http://127.0.0.1:{args.port} run_dir={run_dir}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        replay.shutting_down.set()
        app.stop.set()
        if app.worker is not None:
            app.worker.join(timeout=12)
        if app.worker is None or not app.worker.is_alive():
            audio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
