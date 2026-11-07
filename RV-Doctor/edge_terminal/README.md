# RV-Doctor K1 语音终端

终端在 K1 上采集耳麦录音，使用 SenseVoice 识别，由本机 Qwen3.5-2B 判断意图。页面提供实时进度和结果。任务包括执行固定矩阵乘法体检、解读最近一份体检报告、查看四线程与八线程实测对照和读取当前系统指标。解析失败或收到 `unknown` 时，页面给出提示，派发器跳过执行。

## 板端运行

终端依赖 Bianbu Linux 上的 `arecord`、FFmpeg、GCC/OpenMP、已有的 `llama-server`，以及阶段 1 的 SenseVoice 模型、Python venv 和 ES8326 录音节点访问权限。[阶段 1 环境与启动](../../板端验证/asr/README.md)

把本目录下的 Python 和 HTML 文件复制到 `/home/haotian/rv-doctor-edge-validation/terminal`。再将以下依赖文件复制到同一目录。目录由运行服务的用户 `haotian` 持有。

- `RV-Doctor/docs/edge/acceptance-cases.json`
- `RV-Doctor/benchmarks/b02_matrix_mul.c`
- `RV-Doctor/benchmarks/expected/b02_matrix_mul.out`
- `板端验证/asr/prepare_audio.py`
- `板端验证/asr/acceptance_eval.py`

在板端以 `haotian` 启动本机千问服务。

```bash
/usr/bin/llama-server \
  -m /home/haotian/muse-qwen35/models/Qwen3.5-2B-Q4_K_M.gguf \
  --alias qwen3.5-2b --host 127.0.0.1 --port 18080 --parallel 1 \
  -t 4 -tb 4 -c 2048 -b 128 -ub 32 --reasoning off
```

准备固定体检报告和两组对照数据，然后启动终端。

```bash
cd /home/haotian/rv-doctor-edge-validation/terminal
umask 077
python3 tasks.py --base . --seed          # --base 必须给，否则报告与对照数据落错目录
../asr/venv/bin/python terminal.py --model-dir ../asr/models/sensevoice --port 18793   # --model-dir 必填
```

Windows 端使用 ADB 转发后，在浏览器打开 [K1 语音终端](http://127.0.0.1:18793/)。板端浏览器也可直接打开相同的地址。当前临时服务名为 `rv-doctor-edge-llm.service` 和 `rv-doctor-edge-terminal.service`。

```powershell
& 'D:\Android\Sdk\platform-tools\adb.exe' -s BPMIK102161283023 forward tcp:18793 tcp:18793
```

## 复跑固定验收

文本模式直接提交 20 条参考文本，分别记录 JSON 合法性、意图与实际派发。音频模式复用阶段 1 的 10 条同句原始录音，再用页面的「补录 10 句」采集余下用例。补录和运行终端录音均由用户手动开始。

```bash
../asr/venv/bin/python validate.py --mode text_intent
../asr/venv/bin/python validate.py --mode audio_e2e \
  --source-results ../asr/results/rounds/round-6e90e6b91134/results.json \
  --model-dir ../asr/models/sensevoice
```

验收报告写入 `validation/<run_id>/report.json`。录音和源证据只保留在板端目录。两个模式分别要求 20 条 JSON 全部合法、至少 18 条意图正确、四条未知请求均无派发。语音端到端还要求已派发任务成功。板端采样时钟尚未同步，耗时来自单调计时器。

2026-11-07 的 K1 验收中，文本模式意图正确 20/20，语音端到端模式意图正确 18/20。两个模式均有 20/20 条合法 JSON，四条未知请求均无派发。语音端到端按真实 WAV 复跑，文件输入到任务结果的中位耗时为 39.01 秒，其中千问本地 API 响应时长中位数为 34.10 秒。[文本验收](../../板端验证/edge/results/text_intent-65d85f890736.json) · [语音端到端验收](../../板端验证/edge/results/audio_e2e-c2413d24143c.json)

## 同一录音的页面计时

`benchmark.py` 在独立目录内把固定 WAV 送入相同的终端页面和板端处理链。它在页面进入录音状态前核对输入哈希，点击停止后开始真实前处理、SenseVoice 识别、千问分类和固定任务派发。采集端使用文件回放。用浏览器同一单调时钟计停止按钮点击到任务结果可见，记录前端刷新和传输耗时。

```bash
base=/home/haotian/rv-doctor-edge-validation/terminal
asr=/home/haotian/rv-doctor-edge-validation/asr
llm_pid=$(systemctl show -p MainPID --value rv-doctor-edge-llm.service)
"$asr/venv/bin/python" "$base/benchmark.py" --base "$base" \
  --model-dir "$asr/models/sensevoice" --llm-pid "$llm_pid" --port 18794 \
  --source "$asr/results/rounds/round-6e90e6b91134/ASR-01-23fb5dba/raw.wav" \
  --sha256 4fc11e61189abe3d0192b8bf59410581883f5b6ca2eb44d1ccfd167264fb2631
```

回放配置写入 `measurements/<run_id>/benchmark.json`。每轮 `result.json` 保存分段时长和任务结果，`memory.json` 保存以 250 毫秒为目标间隔的采样值。进程 RSS、cgroup 用量和系统可用内存分别计量。

2026-11-07 的三轮回放从停止到可见结果耗时 39.239、10.318、10.306 秒。首轮千问预填充为 30.028 秒，后两轮复用 122 个提示词 token，预填充约 1.17 秒。ASR 调用耗时 3.59–3.65 秒，任务派发约 0.51 秒。回放服务和千问服务的同一时刻 cgroup 用量合计峰值为 2522.36 MiB，整机可用内存最低为 13163.49 MiB。三轮点击停止后都观察到录音提示短暂回跳约 0.46 秒，浏览器计时终点是任务结果实际可见。[三轮测量](../../板端验证/edge/results/replay-20d8082a07e7.json)

## 千问物理批大小实测

板端采用 `-ub 32`。和 `-ub 128` 对照时，较小的物理批在固定提示词内留下可复用的状态。20 条相同音频复跑的 JSON、意图和派发逐条一致，文件输入到任务结果中位耗时由 39.01 秒降至 18.00 秒。首条千问响应仍约 34 秒，换句后续请求复用 90 个提示词 token。同一句连续提交的第二次千问响应由 4.71 秒增至 12.26 秒。[板端单变量对照](../../板端验证/edge/results/ubatch-128-vs-32-comparison.json) · [板端服务器版本源码](https://github.com/spacemit-com/llama.cpp/blob/17ce6aa/tools/server/server-context.cpp#L2998-L3083)
