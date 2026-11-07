# 阶段 1：K1 本地 ASR 验证

日期：2026-11-06（Asia/Shanghai）。验收定义见 ../../RV-Doctor/docs/edge/acceptance-cases.json。

## 已完成的技术验证

- 官方 spacemit_asr **1.0.2、cp312、linux_riscv64** wheel 已在板端独立 venv 中安装和导入。wheel SHA256 为 c4900d66fb5cf30cc7e910a669f929fb59aa40ad0e70be7b3cafa012ac0a41f9。
- 官方 SenseVoice 的三份资源已下载、传输并在板端核对哈希。没有安装 K3 sm-sdk 全家桶或升级系统。
- 用已有真实耳麦录音做 16 kHz 单声道 PCM16 输入，明确选择 CPU、4 线程、中文。
- 9.5 秒音频识别为“喂喂喂……”；模型初始化约 6.00 秒、识别墙上时间约 3.09 秒、RTF 约 0.325。
- 这段录音的精确参考次数未预先冻结，只作为组件烟雾测试，**不计正式 CER**。

证据：[完整结果](results/sensevoice-smoke-cpu.json)、[原始日志](results/sensevoice-smoke-cpu.log)、[音频准备参数](input/mic-smoke-16k-mono.prepare.json)、[下载与哈希](downloads/manifest.json)。

## 正式验收状态

2026-11-07，`round-19e7f20b8f73` 的 10 条真实录音均已完成。整体 CER 为 81 ÷ 76 = 106.58%，该轮未达标。每条转写均有整句或近似整句重复。[逐句评分](results/round-19e7f20b8f73-assessment.json)

复核时从原始 WAV 的两个互不重叠的时间段分别识别。ASR-08 在 0.75–4.95 秒和 4.95–10.25 秒都识别出「现在可用内存还有多少」。ASR-10 在 0.75–2.30 秒和 2.30–5.00 秒都识别出「弄一下」。分段仅用于定位重复来源，正式分数沿用整段识别结果。

单遍朗读复测 `round-6e90e6b91134` 收齐 10 条，现场采样整体 CER 为 22 ÷ 76 = 28.95%。5 条完全正确。ASR-04 仅返回标点，规范化后按 9 个删除计分。板端已逐一核对原始及处理后音频的 SHA256。[复测逐句评分](results/round-6e90e6b91134-assessment.json)

同一批 10 条录音在 K1 上各自完整复跑两次，使用冻结参考与评分规则。单独关闭 SenseVoice 自动静音裁切得到 17 ÷ 76 = 22.37%。[仅关闭裁切的评分](results/replay-vad-off-b469e9a54dc9-assessment.json)

关闭自动静音裁切后，对每条 16 kHz 单声道输入统一应用两级 110 Hz 高通、12 dB 增益和峰值限制，再用同一 SenseVoice 模型在 CPU 4 线程推理。`replay-filter-6d669d9d6a4f` 得到 14 ÷ 76 = **18.42%**，通过阶段 1 的 `audio_asr` 文件识别门槛。10 条识别耗时为 3.57–3.67 秒/条，统计范围为 SDK 文件识别调用。该配置依据复测录音选定，成绩来自同源文件复跑。现场采样成绩单独保留。[逐句评分](results/replay-filter-6d669d9d6a4f-assessment.json) · [配置、哈希与识别耗时](results/replay-filter-6d669d9d6a4f-results.json)

- CER 规则：NFKC、英文小写、移除 Unicode 空白和标点，按字符 Levenshtein 距离汇总。
- 必须 10 条音频/转写/来源哈希齐全，汇总 CER <=20% 才通过。
- 评分器 20 项纯合成逻辑测试通过；采样状态/HTTP 23 项合成测试通过。这些不代替真实 SDK 或录音验收。

## 手动采样页

`collector.html` 与 `capture_acceptance.py` 是阶段 1 的采样工具，不是已经完成的正式产品。

1. 用户选择固定句子并点击开始。麦克风准备 0.75 秒期间不读句子。
2. 页面出现“现在朗读”后，将参考句完整读一遍，读完点击停止。最长采集 12 秒。
3. 板端完成音频准备和本地识别，保存实际文本与评分，不修改参考文字。
4. 同一轮已识别样本不可覆盖；复测要新开完整一轮，旧轮完整保留。
5. 若采集有效而识别失败，重试使用相同音频，不悄悄换音频。

原始音频、处理副本、哈希、配置、模型版本、识别输出和评分保存在板端 `results/rounds/<round-id>/`。`current-round.json` 标明当前轮。

## 板端环境与启动

工作目录：`/home/haotian/rv-doctor-edge-validation/asr`。

启动前将最新辅助文件、冻结样本和 deploy-manifest.json 部署到该目录并校验哈希。当前设备旧 ADB 在长目标路径中出现过文件名截断，因此传输时使用指向该目录的短临时符号链接 `/tmp/rvk1asr`；重启后需重新创建。模型文件使用 `/tmp/rvk1sv`。

```bash
# 在已有 ADB root shell 内运行；程序核对部署哈希并仅为当前录音/控制节点准备 ACL。
python3 /home/haotian/rv-doctor-edge-validation/asr/setup_collector.py
```

服务由普通用户 haotian 运行，只监听板端 127.0.0.1:18791。没有点击开始时不录音。设备访问权限仅针对当前录音节点与控制节点，重启后失效；原 ACL 保存到 results/audio-acl-before-<boot-id>.txt。

Windows 端建立 USB 转发后，真实服务入口为 http://127.0.0.1:18791/ 。它依赖板子保持 USB 数据连接和服务运行。

```powershell
& 'D:\Android\Sdk\platform-tools\adb.exe' -s BPMIK102161283023 forward tcp:18791 tcp:18791
```

本地只读布局检查曾使用 18792 端口与 layout-fixture-only 状态，**不是板端采样或正式成绩**，不可用来替代真实验收。

## 验证程序

- `download_resources.py`：固定官方资源下载和校验，只提取模型三份常规文件。
- `prepare_audio.py`：保留源录音，做有抗混叠滤波的整倍数降采样，记录增益/裁剪和哈希；不默默接受新增削波。
- `smoke_probe.py`：单文件 SDK 技术验证，显式标为非正式验收。
- `acceptance_eval.py`：冻结样本评分；缺样本/缺来源/改写 gold 不可通过。
- `capture_acceptance.py`：手动录音、识别和整轮结果保存。
- `setup_collector.py`：部署哈希、当前录音设备访问和前台启动准备。

板子时间尚未同步。轮次使用不含日期的唯一 ID，计时使用 monotonic；板端时钟值仅以明确标注的字段保存，不当作客户端真实日期。
