#!/bin/sh
# 阶段 5.4 异常与恢复套件（板端，root shell 内运行）
#
# 覆盖四种状态：
#   1) 千问在线 + 合法意图      -> 期望派发
#   2) 千问在线 + 越界意图      -> 期望零派发
#   3) 千问离线 + 合法意图      -> 期望可读错误 + 零派发
#   4) 重建千问后 + 合法意图    -> 期望恢复派发
#
# 注意：rv-doctor-edge-llm.service 是 systemd-run 的临时单元，
# systemctl stop 之后单元即被销毁，必须用 systemd-run 重建（见下方命令）。
#
# 用法（Windows）：
#   adb push run_recovery_suite.sh /tmp/ && adb shell 'sh /tmp/run_recovery_suite.sh'
# 输出：/tmp/recovery-suite.jsonl，一行一条记录。

set -u
BASE=/home/haotian/rv-doctor-edge-validation/terminal
PY=/home/haotian/rv-doctor-edge-validation/asr/venv/bin/python
OUT=/tmp/recovery-suite.jsonl
MODEL=/home/haotian/muse-qwen35/models/Qwen3.5-2B-Q4_K_M.gguf

: > "$OUT"
cd "$BASE" || exit 1

run_probe() {
    # $1 = label
    "$PY" /tmp/rvprobe.py --base . --label "$1" >> "$OUT" 2>>"$OUT" || true
}

echo "=== 1) 千问在线：合法意图 ==="
run_probe valid_up

echo "=== 2) 千问在线：越界意图 ==="
run_probe unknown

echo "=== 3) 千问离线 ==="
systemctl stop rv-doctor-edge-llm.service 2>/dev/null
sleep 2
run_probe valid_down

echo "=== 4) 重建千问并等待就绪 ==="
systemd-run --unit=rv-doctor-edge-llm --uid=haotian --gid=haotian \
    --working-directory=/home/haotian --property=Restart=no \
    /usr/bin/llama-server -m "$MODEL" --alias qwen3.5-2b \
    --host 127.0.0.1 --port 18080 --parallel 1 -t 4 -tb 4 \
    -c 2048 -b 128 -ub 32 --reasoning off >/dev/null 2>&1

i=0
while [ $i -lt 40 ]; do
    sleep 2
    if curl -fsS --max-time 2 http://127.0.0.1:18080/health >/dev/null 2>&1; then
        echo "llm healthy after $((i * 2 + 2))s"
        break
    fi
    i=$((i + 1))
done

echo "=== 5) 恢复后复测 ==="
run_probe valid_recovery

echo "=== done ==="
wc -l "$OUT"