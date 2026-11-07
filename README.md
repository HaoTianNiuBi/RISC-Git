# RV-Doctor · 面向 RISC-V 的代码性能诊断与端侧语音交互终端

> 参赛作品：第二届 CIE 全国 RISC-V 高水平创新及应用大赛 · CIE-进迭时空赛道（赛题 31）
> 主平台：进迭时空 K1（MUSE Pi Pro）· Bianbu LXQt · 多模态（ASR + LLM）本地推理

**一句话**：把「给 C 代码做一次 RISC-V 体检」做成可复核的流水线，再把它搬到 K1 上，用**离线语音**指挥它体检、解释报告、比较配置——所有性能数字都来自真实编译 / 运行 / 日志解析，AI 只负责在约束内做建议，结论由验证闭环给出。

*English*: RV-Doctor cross-compiles C sources for RISC-V, runs them on QEMU bare-metal, parses instruction traces into dynamic instruction statistics, and lets a rule engine plus an LLM propose optimizations that must pass compile → output-identical → measured-faster gates before being accepted, with automatic rollback. The K1 edge build wraps this pipeline in an offline voice interface (local ASR → local LLM intent → restricted task execution).

**成果包**：不 clone 也能看——[Releases](../../releases) 的 `v1.0-20261107` 里有一个 zip（168 个文件 / 0.44 MB），按「技术文档 / 源码 / 证据」三栏整理，不含模型权重、原始录音与工具链。

---

## 1. 三条技术线

| 线 | 位置 | 形态 | 状态 |
|---|---|---|---|
| **A. 主机侧诊断流水线** | [`RV-Doctor/`](RV-Doctor/) | Windows 上的 Streamlit 应用 + RISC-V 交叉编译 + QEMU 裸机 + trace 解析 + LLM 优化闭环 | 已打通「体检 → 建议 → 候选验证 → 报告」，含 10 个基准与冻结快照 |
| **B. K1 端侧语音终端** | [`RV-Doctor/edge_terminal/`](RV-Doctor/edge_terminal/) | 触屏终端：录音 → 本地 SenseVoice ASR → 本地 Qwen 意图 → 固定任务执行与结果展示 | 已上板，四类任务可用；20 条端到端验收 18/20 |
| **C. 板端验证与证据** | [`板端验证/`](板端验证/) | K1 真机采集、模型验证、ASR 采集工具与全部原始结果 | 阶段 1–4 记录齐全，逐条可复算 |

两条线共用同一套思想：**测量归测量，AI 归 AI，结论必须可复算**。模拟指令数与 K1 真机数据在报告里分别标注，不混用。

## 2. 当前进度（如实记录）

| 阶段 | 验收口径 | 状态 |
|---|---|---|
| 0 范围与基线 | 设计、执行记录、验收样本定义、源文件基线落盘 | 完成 |
| 1 本地 ASR | 固定 WAV 离线识别 → 10 条真实录音汇总 CER ≤ 20% | 现场单遍 28.95%（未达标）；同源 10 条统一前处理复跑 **18.42% 通过**，两项分别记账 |
| 2 最小交互 | 20 条端到端样本 ≥ 18 条任务正确，JSON 合法率 100% | 完成：文本 20/20、语音端到端 18/20，JSON 合法 20/20，`unknown` 误派发 0 |
| 3 真机测量 | 同一输入 ≥ 3 次，记录 ASR / LLM 首字与总耗时 / 内存 | 完成：同一 WAV 回放 3 次，停止到结果 39.239 / 10.318 / 10.306 s，含内存采样 |
| 4 配置调优 | 每次只改一个变量，正确率不降且指标改善才接受 | 完成：仅改 `-ub 128 → 32`，API 中位 34.10 → 12.66 s（−62.86%），20 条结果逐条一致 |
| 5 交付 | 可重启、连续 10 次交互不崩溃、异常可恢复、材料齐全 | 进行中 |

> 验收状态以证据文件为准：阶段 1 已完成 10 条真实录音——现场单遍 28.95% 未过门槛，同源统一前处理复跑 18.42% 通过，两个口径分别记账。早期 9.5 秒「喂喂喂」录音只用于验证链路，**不计正式 CER**。

## 3. 仓库结构

```text
.
├── RV-Doctor/                    # 主机侧诊断流水线
│   ├── app.py                    # Streamlit 界面
│   ├── pipeline.py               # 体检 / 优化 / 验证 / 报告编排
│   ├── compiler/                 # 交叉编译、反汇编、裸机启动与链接脚本
│   ├── profiler/                 # QEMU 运行、trace 解析、指标与能耗估算
│   ├── optimizer/               # R1～R9 规则、LLM 接口、候选验证
│   ├── reports/                  # Markdown 报告生成器 + 已整理实测数据
│   ├── benchmarks/               # 10 个 C 基准（含期望输出、冻结快照、候选变体）
│   ├── tests/                    # 单元测试与真实工具链集成测试
│   ├── scripts/                  # 自检、端到端演示、数据整理
│   ├── edge_terminal/            # K1 触屏语音终端（录音 → ASR → 意图 → 任务）
│   └── docs/                     # 设计、使用、验证与参赛文档
└── 板端验证/                      # K1 真机验证与证据
    ├── audio/                    # 耳麦采集信号统计（原始录音不入库）
    ├── edge/                     # 终端端到端验收、回放计时与调优对照
    ├── llm/                      # 本地千问 API 单次探测结果
    ├── asr/                      # 阶段 1：ASR 采集工具、模型清单、评分器、结果
    ├── baseline/                 # K1 改造前的源文件基线（129 文件，含 SHA256）
    └── README.md                 # 板端验证记录
```

## 4. 快速开始（主机侧）

需要 **Python 3.11+**（配置读取使用标准库 `tomllib`），推荐纯 ASCII 路径，例如 `D:\RISC-V-first`。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

工具链（xPack `riscv-none-elf-gcc` 与 QEMU）按 [`docs/04-环境搭建指南.md`](RV-Doctor/docs/04-环境搭建指南.md) 放到 `RV-Doctor/tools/`，或修改 `RV-Doctor/config.toml` 指向已有安装：

```text
tools/gcc/bin/riscv-none-elf-gcc.exe
tools/gcc/bin/riscv-none-elf-objdump.exe
tools/qemu/qemu-system-riscv64.exe
tools/qemu/qemu-system-riscv32.exe
```

启动体检台：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501
```

全量自检（缺外部工具时相关用例自动跳过，注意区分「离线通过」与「完整链路通过」）：

```powershell
.\scripts\run_all_checks.bat
```

## 5. 快速开始（K1 端侧）

两个板端服务，都只监听 `127.0.0.1`：

| 服务 | 端口 | 说明 |
|---|---|---|
| ASR 采集页 | 18791 | 阶段 1 的 10 条固定样本采集与评分，见 [`板端验证/asr/README.md`](板端验证/asr/README.md) |
| 语音终端 | 18793 | 录音 → SenseVoice → 千问意图 → 任务执行，见 [`RV-Doctor/edge_terminal/README.md`](RV-Doctor/edge_terminal/README.md) |

终端依赖板端 `arecord`、FFmpeg、GCC/OpenMP、已有的 `llama-server` 与阶段 1 的 SenseVoice 模型和 venv；部署步骤、验收复跑命令都在上面的 README 里。模型文件不入库（227 MB），用 [`download_resources.py`](板端验证/asr/download_resources.py) 按 [`manifest.json`](板端验证/asr/downloads/manifest.json) 下载并核对 SHA256 还原。

## 6. 已实测的数据（截至 2026-11-07）

> 下表每个数字都可用 `python 板端验证/edge/check_numbers.py` 一次性对照证据文件校验（13 项检查，全绿输出 `ALL NUMBER CHECKS PASSED`）。

| 项目 | 数值 | 证据 |
|---|---|---|
| 基准集基线（rv64gc/O0，10 个基准） | 动态指令数确定、可复算 | `RV-Doctor/reports/data/baseline_overview.json` |
| 同一浮点程序在三种架构的退化（-O0） | 见对比报告 | `RV-Doctor/reports/data/b06_arch_compare.md` |
| 编译等级边界（rv64gc，-O0…-Os） | 见对比报告 | `RV-Doctor/reports/data/b09_opt_compare.md` |
| 阶段 1 现场采样 CER（10 条） | 22/76 = **28.95%**，5 条全对，未达标 | `板端验证/asr/results/round-6e90e6b91134-assessment.json` |
| 阶段 1 同源复跑 CER（统一前处理） | 14/76 = **18.42%**，通过 ≤20% 门槛 | `板端验证/asr/results/replay-filter-6d669d9d6a4f-assessment.json` |
| 文本意图验收（20 条） | JSON 合法 20/20，意图 20/20，未知误派发 0；API 中位 35.08 s | `板端验证/edge/results/text_intent-65d85f890736.json` |
| 语音端到端验收（20 条真实音频） | JSON 20/20，意图 18/20，未知误派发 0；文件→结果中位 39.01 s | `板端验证/edge/results/audio_e2e-c2413d24143c.json` |
| 同一录音页面计时（3 次） | 停止→结果 39.239 / 10.318 / 10.306 s；cgroup 峰值 2522.36 MiB | `板端验证/edge/results/replay-20d8082a07e7.json` |
| 单变量调优 `-ub 128 → 32` | API 中位 34.10 → **12.66 s（−62.86%）**，文件→结果 39.01 → 18.00 s，20 条逐条一致 | `板端验证/edge/results/ubatch-128-vs-32-comparison.json` |
| SenseVoice 文件识别（9.5 s 音频，CPU 4 线程） | 墙上 3.09 s，RTF ≈ 0.325，模型初始化 ≈ 6.00 s | `板端验证/asr/results/sensevoice-smoke-cpu.json` |
| 本地千问意图分类（Qwen3.5-2B Q4_K_M，4 线程） | 首内容 12.723 s / 完整 15.609 s，50 进 7 出 token，RSS ≈ 1.87 GiB | `板端验证/llm/qwen35-2b-4threads-single-request.json` |
| 耳麦采集 | 48 kHz PCM16 双声道，人声可听（用户确认）；录音不入库，只留统计 | `板端验证/audio/mic-20261106-take4-48k-stereo.analysis.json` |

## 7. 边界声明（诚实原则）

- **不测真实芯片功耗**：本仓库不声称测量任何硬件的功耗、温度或主频。
- **能耗是相对估算**：`Σ(指令类别计数 × 可配置权重)`，只用于同基准前后对比，**不替代硬件功耗测量**。
- **TCG 不模拟缓存**：QEMU 是指令级模拟，「缓存友好性」只作静态提示，不作为实测指标。
- **主指标是动态指令数**：同一输入两次运行结果一致；墙上时间仅作参考。
- **单次探测 ≠ 基准**：早期的单次请求数字只作链路验证，稳定性结论以 20 条验收和 3 次重复测量为准。
- **时延未达标就写未达标**：停止到结果的现场计时为 39.239 / 10.318 / 10.306 s，调优后文件输入到结果中位 18.00 s，**均未达到 4 s 的评分目标**，此处不修饰。
- **CER 分口径记账**：现场单遍朗读 28.95% 未通过；18.42% 是同源 10 条录音换统一前处理后复跑的结果，两者分别标注，不混为一条成绩。
- **等价性有限**：输出一致性校验针对当前测试输入，不构成对所有输入的等价性证明。

## 8. 文档导航

| 文档 | 内容 |
|---|---|
| [`方案书.md`](方案书.md) | 参赛方案书：摘要、引言、技术实现、开发计划、预期成果、应用价值与市场潜力、附录 |
| [`RV-Doctor/docs/01-项目概述.md`](RV-Doctor/docs/01-项目概述.md) | 背景、目标、创新点与边界 |
| [`RV-Doctor/docs/02-需求说明.md`](RV-Doctor/docs/02-需求说明.md) | 用户故事、功能与验收标准 |
| [`RV-Doctor/docs/03-系统架构设计.md`](RV-Doctor/docs/03-系统架构设计.md) | 模块、接口、数据模型与技术选型 |
| [`RV-Doctor/docs/04-环境搭建指南.md`](RV-Doctor/docs/04-环境搭建指南.md) | Python、GCC 与 QEMU 安装 |
| [`RV-Doctor/docs/06-测试与验证方案.md`](RV-Doctor/docs/06-测试与验证方案.md) | 数据口径、基准与验证闭环 |
| [`RV-Doctor/docs/09-参赛技术文档.md`](RV-Doctor/docs/09-参赛技术文档.md) | 技术说明与实测结果 |
| [`RV-Doctor/docs/superpowers/specs/2026-11-06-k1-edge-design.md`](RV-Doctor/docs/superpowers/specs/2026-11-06-k1-edge-design.md) | K1 端侧改造设计与验收口径 |
| [`RV-Doctor/docs/edge/验收样本说明.md`](RV-Doctor/docs/edge/验收样本说明.md) | 阶段 1 固定样本与 CER 规则 |
| [`RV-Doctor/docs/edge/执行计划.md`](RV-Doctor/docs/edge/执行计划.md) | 阶段 0–4 检查表与逐项实测记录 |
| [`RV-Doctor/edge_terminal/README.md`](RV-Doctor/edge_terminal/README.md) | K1 语音终端的部署、验收复跑与实测说明 |
| [`板端验证/README.md`](板端验证/README.md) | K1 真机验证记录（耳麦、LLM、ASR 缺口） |

## 9. 许可与致谢

- 本项目源码以 [LICENSE](LICENSE) 授权。
- 进迭时空官方 SDK、模型与文档的版权归其各自权利人所有；本仓库只按官方渠道下载使用，不转售、不重新分发模型权重。
- 感谢进迭时空提供 K1 MUSE Pi Pro 与 Bianbu 系统环境。