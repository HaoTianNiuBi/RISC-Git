"""pipeline 单元测试（docs/06 §7；工具链无关部分不依赖 gcc/qemu）。"""
from __future__ import annotations

import re

import pytest

import pipeline
from tests.conftest import requires_qemu
from tests.toolchain import requires_gcc, requires_objdump
from pipeline import (
    MAX_SRC_BYTES,
    PipelineError,
    new_task_id,
    run_checkup,
    sanitize_name,
    stdout_matches,
)

# ---- 任务号与文件名清洗 --------------------------------------------------------
def test_new_task_id_format():
    tid = new_task_id()
    assert re.fullmatch(r"t-\d{8}-\d{6}-[0-9a-f]{4}", tid)


def test_sanitize_name_strips_non_ascii():
    assert sanitize_name("b01_sum_array.c") == "b01_sum_array.c"
    # 中文/空格 → 下划线（连续非法字符折叠为一个），扩展名保留
    assert sanitize_name("你好 世界.c") == "_.c"
    assert sanitize_name("矩阵.c") == "_.c"
    assert sanitize_name("///") == "app.c"           # 全非法 → 兜底名
    assert sanitize_name("") == "app.c"
    assert sanitize_name(".hidden") == "app.c"       # 纯扩展名 → 兜底


def test_run_checkup_rejects_non_ascii_workroot(tmp_path):
    """中文工作目录必须提前报错，而不是让 QEMU 报 cryptic 的日志打开失败。"""
    src = tmp_path / "hello.c"
    src.write_text("#include <stdio.h>\nint main(){return 0;}\n", encoding="utf-8")
    cn_root = tmp_path / "工作目录"
    cn_root.mkdir()
    with pytest.raises(PipelineError, match="ASCII"):
        run_checkup(src, workroot=cn_root)


# ---- golden 比对规则（docs/06 §4）----------------------------------------------
def test_stdout_matches_identical_and_newline_tolerance():
    ok, _ = stdout_matches("sum=5050\n", "sum=5050\n")
    assert ok
    ok, _ = stdout_matches("sum=5050\r\n", "sum=5050\n")      # \r\n 统一
    assert ok
    ok, _ = stdout_matches("sum=5050", "sum=5050\n")          # 末尾至多一个换行差异
    assert ok
    ok, _ = stdout_matches("sum=5050\n\n", "sum=5050\n")      # 两个换行 = 实质差异
    assert not ok


def test_stdout_matches_reports_first_diff():
    ok, msg = stdout_matches("line1\nlineX\n", "line1\nline2\n")
    assert not ok
    assert "line 2" in msg and "lineX" in msg


# ---- 调用方错误（不进任务目录，直接抛）------------------------------------------
def test_run_checkup_rejects_bad_arch_and_opt(tmp_path):
    with pytest.raises(PipelineError, match="架构"):
        run_checkup("int main(){return 0;}", arch="rv999x")
    with pytest.raises(PipelineError, match="优化等级"):
        run_checkup("int main(){return 0;}", opt="O9")


def test_run_checkup_rejects_oversized_source():
    big = "int x;\n" * (MAX_SRC_BYTES // 6 + 10)
    with pytest.raises(PipelineError, match="上限"):
        run_checkup(big)


def test_run_checkup_missing_source_file(tmp_path):
    with pytest.raises(PipelineError, match="不存在"):
        run_checkup(tmp_path / "nope.c")


@requires_gcc
@requires_qemu
def test_run_checkup_accepts_string_path_and_empty_stdout(tmp_path, workdir):
    src = tmp_path / "silent.c"
    src.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    r = run_checkup(str(src), arch="rv64i", opt="O0", workroot=workdir)
    assert r.state == "DONE", r.error
    assert r.stdout == ""
    assert r.metrics["run"]["exit_code"] == 0


# ---- 错误路径（真实工具链：编译失败 → 结构化返回，不抛业务异常）------------------
@requires_gcc
@requires_qemu
def test_run_checkup_compile_failure_returns_state(workdir):
    bad = workdir / "bad.c"
    bad.write_text("int main( { this is not C\n", encoding="utf-8")
    r = run_checkup(bad, benchmark="bad", workroot=workdir)
    assert r.state == "COMPILE_FAILED"
    assert r.error
    assert not r.ok
    # 证据链：build.log 留存
    assert r.workdir is not None and (r.workdir / "build.log").exists()
    # 步骤轨迹：receive 成功、compile 失败，无后续步骤
    names = [s.name for s in r.steps]
    assert names == ["receive", "compile"]


@requires_gcc
@requires_qemu
def test_run_checkup_end_to_end_hello(workdir):
    # workroot 必须用仓库内 ASCII 路径（conftest workdir fixture）：
    # QEMU for Windows 无法在含非 ASCII 字符的路径下打开 -D trace 日志
    # （docs/04 FAQ 中文路径问题；pytest tmp_path 位于 C:\Users\<用户名>\ 下必踩）。
    r = run_checkup(
        "#include <stdio.h>\n"
        "int main(void){int s=0;for(int i=1;i<=100;i++)s+=i;"
        'printf("sum=%d\\n",s);return 0;}\n',
        arch="rv64gc", opt="O0", benchmark="hello", workroot=workdir,
        verify_determinism=True,
    )
    assert r.state == "DONE", r.error
    assert "sum=5050" in r.stdout
    assert r.metrics["dynamic"]["total"] > 0
    assert r.metrics["meta"]["arch"] == "rv64gc"
    assert r.metrics["run"]["exit_code"] == 0
    # 证据链五件套落盘
    for f in ("build.log", "disasm.txt", "stdout.txt", "trace.log", "metrics.json"):
        assert (r.workdir / f).exists(), f
    # 步骤轨迹完整（含 determinism）
    names = [s.name for s in r.steps]
    assert names == ["receive", "compile", "disasm", "run", "parse",
                     "aggregate", "determinism"]


@requires_gcc
@requires_qemu
def test_run_checkup_rejects_truncated_trace(workdir, monkeypatch):
    config = pipeline.load_config()
    config["limits"] = {"trace_max_bytes": 128}
    monkeypatch.setattr(pipeline, "load_config", lambda: config)
    result = run_checkup("int main(void) { return 0; }", arch="rv64i", workroot=workdir)
    assert result.state == "PARSE_FAILED"
    assert "截断" in result.error


@requires_gcc
@requires_qemu
def test_run_checkup_rejects_trace_without_program_execution(workdir, monkeypatch):
    from profiler.trace_parser import TraceStats

    monkeypatch.setattr(pipeline, "parse_trace",
                        lambda path, max_bytes=None: TraceStats())
    result = run_checkup("int main(void) { return 0; }", arch="rv64i", workroot=workdir)
    assert result.state == "PARSE_FAILED"
    assert "没有程序区执行记录" in result.error


@requires_gcc
@requires_qemu
def test_run_checkup_detects_second_run_output_change(workdir, monkeypatch):
    original_run = pipeline.runner.run_baremetal
    runs = 0

    def changed_second_output(*args, **kwargs):
        nonlocal runs
        result = original_run(*args, **kwargs)
        runs += 1
        if runs == 2:
            result.stdout += "different"
        return result

    monkeypatch.setattr(pipeline.runner, "run_baremetal", changed_second_output)
    result = run_checkup("int main(void) { return 0; }", arch="rv64i",
                         workroot=workdir, verify_determinism=True)
    assert result.state == "NONDETERMINISTIC"
    assert "输出不一致" in result.error
