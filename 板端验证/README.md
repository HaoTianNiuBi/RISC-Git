# K1 板端基础验证记录

验证日期：2026-11-06（Asia/Shanghai）。这是实际检查记录，与官方资料快照分开保存。

## 耳麦采集

- 使用开发板 AUDIO 口连接单插头、带麦克风的 3.5mm 耳麦。
- 重新插紧并启动后，驱动同时报告耳机与麦克风已插入（Headset Jack switch 状态为 20）。此前状态为 4，仅报告耳机插入。
- 当前声卡原生采样率为 48 kHz、PCM16；16 kHz 采集配置曾被驱动拒绝。
- 重启前后声卡编号由 1 变为 0，后续应使用稳定设备名 `hw:CARD=sndes8326,DEV=0`。
- 第 4 次采集为 10 秒、48 kHz、双声道；回放副本去掉开头 0.5 秒启动瞬态、转单声道、放大 24 dB。用户已确认能听到自己的“喂喂喂”。
- 原始录音与回放副本属于个人语音，**只保留在本地证据目录，不随公开仓库发布**。
- [原始信号统计](audio/mic-20261106-take4-48k-stereo.analysis.json)：时长、电平、削波与频段分布，不含语音内容，可用于复核采集链路。
- 人声可听由用户确认；尚未使用 ASR 转写，也未证明识别准确率或连续采集稳定性。
- 原始收音偏弱，后续需验证输入增益和背景噪声；这次没有修改声卡增益。

采集命令：

```bash
arecord -D 'hw:CARD=sndes8326,DEV=0' -f S16_LE -r 48000 -c 2 -d 10 output.wav
```

## 本地千问 API

模型：已有的 Qwen3.5-2B Q4_K_M GGUF。执行程序：`/usr/bin/llama-server`，由 `haotian` 用户运行。

测试参数：4 线程、2048 上下文、单并发、关闭 reasoning；临时接口仅监听 `127.0.0.1:18080`。

一次合成请求：将“查看内存使用”分类为 `memory`、`cpu` 或 `unknown`，要求仅输出 JSON。

| 指标 | 本次结果 |
|---|---:|
| 实际输出 | `{"intent": "memory"}` |
| 首个内容返回 | 12.723 秒 |
| 完整返回 | 15.609 秒 |
| 输入 / 输出 token | 50 / 7 |
| 提示词处理速度 | 4.035 token/s |
| 生成速度 | 2.426 token/s |
| 请求后进程 RSS | 约 1.87 GiB |

[完整请求测试结果](llm/qwen35-2b-4threads-single-request.json)。上述数字仅来自一次合成请求，不是稳定性、识别准确率或语音端到端性能基准。服务日志显示本次模型初始化约 22.8 秒。

测试后已停止临时模型服务。原有项目、桌面快捷方式和启动脚本均未修改。

## ASR 当前缺口

板内未找到 `asr_file_demo`、`asr_stream_demo`、`spacemit_asr` 或 `sherpa_onnx`；已检查的模型缓存目录未发现 SenseVoice 模型资源。

官方模型库将 SenseVoice 列在 K1 音频模型项中，但当前板端的 SDK、模型、依赖和实际推理兼容性仍需验证。下一步宜先验证单个 16 kHz 单声道 WAV 的本地文件识别。

参考：[ASR README](../K1-官方资料/ai/model-zoo-asr/README.md)、[ASR API](../K1-官方资料/ai/model-zoo-asr/API.md)、[官方模型库](../K1-官方资料/ai/docs-ai/zh/compute_stack/ai_compute_stack/modelzoo.md)。
