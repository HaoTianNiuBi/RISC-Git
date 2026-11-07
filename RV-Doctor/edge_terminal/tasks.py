"""Fixed native benchmark and read-only status tasks for the edge terminal."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import uuid


_BENCHMARK = "b02_matrix_mul"
_THREADS = (4, 8)
_RUNS = 3
_MEASUREMENT = "subprocess_wall_including_startup"
_LATEST = "latest_report.json"
_COMPARISON = "config_comparison.json"


class TaskError(RuntimeError):
    """A fixed task could not produce a verified result."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".report-", suffix=".tmp", delete=False,
        mode="w", encoding="utf-8",
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_report(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(record, dict):
        raise TaskError("已保存记录的格式不正确。")
    return record


def _read_cpu_ticks() -> tuple[int, int]:
    with Path("/proc/stat").open(encoding="ascii") as stream:
        fields = stream.readline().split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise TaskError("无法读取处理器计数。")
    # Guest counters are already included in user/nice.
    ticks = [int(field) for field in fields[1:9]]
    idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
    return sum(ticks), idle


def _measurements_complete(measurement: object, threads: int) -> bool:
    if not isinstance(measurement, dict):
        return False
    elapsed = measurement.get("median_wall_seconds")
    runs = measurement.get("runs")
    return (
        measurement.get("requested_omp_threads") == threads
        and measurement.get("stdout_matches_golden") is True
        and isinstance(runs, list) and len(runs) == _RUNS
        and isinstance(elapsed, (float, int)) and not isinstance(elapsed, bool)
        and math.isfinite(elapsed) and elapsed >= 0
    )


class TaskDispatcher:
    """Dispatch an approved intent to a fixed local task."""

    def __init__(self, base: Path):
        self.base = Path(base).resolve()
        self.state_dir = self.base / "state"
        self._lock = threading.Lock()
        self._handlers = {
            "start_checkup": self._start_checkup,
            "explain_report": self._explain_report,
            "compare_configs": self._compare_configs,
            "query_system": self._query_system,
        }

    def dispatch(self, intent: str) -> dict | None:
        if not isinstance(intent, str):
            return None
        handler = self._handlers.get(intent)
        if handler is None:
            return None
        with self._lock:
            try:
                return {"intent": intent, **handler()}
            except (TaskError, OSError, ValueError, subprocess.SubprocessError) as error:
                return {
                    "intent": intent, "ok": False, "status": "failed",
                    "message": str(error), "data": None,
                }

    def _build(self) -> tuple[Path, bytes, dict]:
        gcc = shutil.which("gcc")
        if gcc is None:
            raise TaskError("未找到本机 gcc，无法编译固定体检基准。")
        source = (self.base / f"{_BENCHMARK}.c").read_bytes()
        golden = (self.base / f"{_BENCHMARK}.out").read_bytes()
        expected = golden.replace(b"\r\n", b"\n")
        if not source.strip() or not expected.strip():
            raise TaskError("固定基准源码或参考输出为空。")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        flags = ["-std=c11", "-O2", "-fopenmp"]
        with tempfile.TemporaryDirectory(dir=self.state_dir, prefix=".build-") as directory:
            work = Path(directory)
            staged_source = work / f"{_BENCHMARK}.c"
            staged_binary = work / _BENCHMARK
            staged_source.write_bytes(source)
            started = time.perf_counter()
            build = subprocess.run(
                [gcc, *flags, str(staged_source), "-o", str(staged_binary)],
                capture_output=True, timeout=30, cwd=work,
            )
            build_wall = time.perf_counter() - started
            if build.returncode:
                detail = build.stderr.decode("utf-8", errors="replace").strip()[-500:]
                raise TaskError(f"固定基准编译失败。{detail}")
            binary_hash = _sha256(staged_binary.read_bytes())
            binary = self.state_dir / f"{_BENCHMARK}-{binary_hash}"
            os.replace(staged_binary, binary)
        return binary, expected, {
            "benchmark": _BENCHMARK,
            "platform": platform.system(),
            "machine": platform.machine(),
            "compiler": gcc,
            "compiler_flags": flags,
            "source_sha256": _sha256(source),
            "golden_sha256": _sha256(golden),
            "binary_sha256": binary_hash,
            "binary_name": binary.name,
            "build_wall_seconds": build_wall,
            "timing_scope": _MEASUREMENT,
        }

    def _measure(self, binary: Path, expected: bytes, threads: int) -> dict:
        env = os.environ.copy()
        env.update(
            OMP_NUM_THREADS=str(threads), OMP_DYNAMIC="FALSE",
            OMP_THREAD_LIMIT=str(max(_THREADS)),
        )
        runs = []
        for _ in range(_RUNS):
            started = time.perf_counter()
            result = subprocess.run(
                [str(binary)], capture_output=True, timeout=10,
                env=env, cwd=self.state_dir,
            )
            elapsed = time.perf_counter() - started
            if result.returncode:
                detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
                raise TaskError(f"固定基准运行失败，退出码 {result.returncode}。{detail}")
            if result.stdout.replace(b"\r\n", b"\n") != expected:
                raise TaskError("矩阵乘法输出与固定参考输出不一致。")
            runs.append({
                "wall_seconds": elapsed, "exit_code": result.returncode,
                "stdout_sha256": _sha256(result.stdout),
            })
        return {
            "requested_omp_threads": threads,
            "stdout_matches_golden": True,
            "stdout": result.stdout.decode("utf-8").rstrip("\r\n"),
            "runs": runs,
            "median_wall_seconds": statistics.median(run["wall_seconds"] for run in runs),
        }

    def _run_benchmark(self, *, compare: bool) -> tuple[dict, dict | None]:
        binary, expected, build = self._build()
        run_id = uuid.uuid4().hex
        base_record = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            **build,
        }
        baseline = self._measure(binary, expected, _THREADS[0])
        report = {**base_record, "measurement": baseline}
        comparison = None
        if compare:
            candidate = self._measure(binary, expected, _THREADS[1])
            comparison = {
                **base_record,
                "configs": {str(_THREADS[0]): baseline, str(_THREADS[1]): candidate},
            }
        _atomic_json(self.state_dir / f"checkup-{run_id}.json", report)
        _atomic_json(self.state_dir / _LATEST, report)
        if comparison is not None:
            _atomic_json(self.state_dir / f"comparison-{run_id}.json", comparison)
            _atomic_json(self.state_dir / _COMPARISON, comparison)
        return report, comparison

    def seed(self) -> dict:
        """Compile and measure both fixed thread settings on the same binary."""
        with self._lock:
            report, comparison = self._run_benchmark(compare=True)
        return {
            "ok": True, "status": "completed",
            "message": "已生成体检报告和四、八线程实测记录。",
            "data": {"report": report, "comparison": comparison},
        }

    def _start_checkup(self) -> dict:
        report, _ = self._run_benchmark(compare=False)
        elapsed_ms = report["measurement"]["median_wall_seconds"] * 1000
        return {
            "ok": True, "status": "completed",
            "message": f"固定矩阵乘法体检完成，输出与参考一致。三次进程运行耗时中位数 {elapsed_ms:.2f} 毫秒。",
            "data": report,
        }

    def _explain_report(self) -> dict:
        report = _read_report(self.state_dir / _LATEST)
        if report is None or not _measurements_complete(report.get("measurement"), _THREADS[0]):
            return {
                "ok": False, "status": "unavailable",
                "message": "当前没有已核对输出的体检报告。请先开始体检。", "data": None,
            }
        elapsed_ms = report["measurement"]["median_wall_seconds"] * 1000
        return {
            "ok": True, "status": "completed",
            "message": (
                f"最近一次体检运行了固定矩阵乘法，三次输出都与参考一致。"
                f"进程运行耗时中位数为 {elapsed_ms:.2f} 毫秒，包含进程启动和 OpenMP 调度。"
            ),
            "data": report,
        }

    def _compare_configs(self) -> dict:
        comparison = _read_report(self.state_dir / _COMPARISON)
        configs = comparison.get("configs") if comparison is not None else None
        if not isinstance(configs, dict) or not all(
            _measurements_complete(configs.get(str(threads)), threads) for threads in _THREADS
        ):
            return {
                "ok": False, "status": "unavailable",
                "message": "当前没有完整的四线程和八线程实测记录。", "data": None,
            }
        four_ms = configs["4"]["median_wall_seconds"] * 1000
        eight_ms = configs["8"]["median_wall_seconds"] * 1000
        return {
            "ok": True, "status": "completed",
            "message": (
                f"同一固定基准运行三次的进程耗时中位数，四线程配置 {four_ms:.2f} 毫秒，"
                f"八线程配置 {eight_ms:.2f} 毫秒。两组输出都与参考一致，耗时包含进程启动和 OpenMP 调度。"
            ),
            "data": comparison,
        }

    def _query_system(self) -> dict:
        meminfo = {}
        with Path("/proc/meminfo").open(encoding="ascii") as stream:
            for line in stream:
                key, _, value = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    meminfo[key] = int(value.split()[0])
        total_kib = meminfo.get("MemTotal", 0)
        available_kib = meminfo.get("MemAvailable", -1)
        if total_kib <= 0 or not 0 <= available_kib <= total_kib:
            raise TaskError("无法读取系统可用内存。")
        sample_started = time.perf_counter()
        total_before, idle_before = _read_cpu_ticks()
        time.sleep(0.2)
        total_after, idle_after = _read_cpu_ticks()
        sample_seconds = time.perf_counter() - sample_started
        total_delta, idle_delta = total_after - total_before, idle_after - idle_before
        if total_delta <= 0 or not 0 <= idle_delta <= total_delta:
            raise TaskError("无法计算当前处理器占用。")
        cpu_percent = 100 * (total_delta - idle_delta) / total_delta
        return {
            "ok": True, "status": "completed",
            "message": (
                f"当前处理器占用 {cpu_percent:.1f}%，"
                f"可用内存 {available_kib / 1024:.0f} MiB，共 {total_kib / 1024:.0f} MiB。"
            ),
            "data": {
                "cpu_busy_percent": cpu_percent, "cpu_sample_seconds": sample_seconds,
                "mem_available_kib": available_kib, "mem_total_kib": total_kib,
                "source": ["/proc/stat", "/proc/meminfo"],
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--seed", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = TaskDispatcher(args.base).seed()
    except (TaskError, OSError, ValueError, subprocess.SubprocessError) as error:
        result = {"ok": False, "status": "failed", "message": str(error), "data": None}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
