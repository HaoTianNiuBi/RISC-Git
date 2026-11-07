# RV-Doctor

**面向 RISC-V 的代码性能诊断与 AI 辅助优化平台。**

在本机输入一份 C 源码，RV-Doctor 会交叉编译、在 QEMU 裸机环境中运行并解析指令轨迹，展示动态指令分布、热点函数和规则诊断；配置 DeepSeek 后，还可以生成优化候选，自动验证输出与指标，再生成对比报告。

当前代码已接通「体检 → 建议 → 候选验证 → 报告」主流程，附带 10 个基准程序、冻结快照和 pytest 测试。网页入口支持粘贴 C 源码或上传单个 `.c` 文件（上限 1 MB）；C++ 工程和网页上传 ELF 尚未接入。

## 工作流程

```text
C 源码
  → RISC-V GCC 编译 → objdump 反汇编与静态分析
  → QEMU 裸机运行 → trace 解析 → metrics.json → 图表与规则诊断
  → DeepSeek 建议（可选）→ 候选源码
  → 重新编译 → 输出比对 → 确定性双跑 → 指标对比
  → 接受最优候选并生成 Markdown 报告，或拒绝候选并保留失败证据
```

优化候选单独保存，网页流程不会覆盖原始源码。验证器支持在指定工作副本的场景下失败回滚。未配置 API 或请求失败时，界面降级为规则诊断模式。

## 快速开始

推荐使用 Windows 和纯 ASCII 项目路径，例如 `D:\RISC-V-first`。Python 需要 **3.11+**（配置读取使用标准库 `tomllib`）。

首次配置可双击 `scripts/install.bat`，自动创建虚拟环境、安装依赖并准备工具链；本地离线工具链包位于 `dist/offline/tools.zip`，没有离线包时脚本尝试在线下载。也可以在项目根目录手动运行 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

按 [环境搭建指南](docs/04-环境搭建指南.md) 准备 xPack RISC-V GCC 与 QEMU。默认工具位置如下，也可以在根目录 `config.toml` 修改：

```text
tools/gcc/bin/riscv-none-elf-gcc.exe
tools/gcc/bin/riscv-none-elf-objdump.exe
tools/qemu/qemu-system-riscv64.exe
tools/qemu/qemu-system-riscv32.exe
```

双击根目录的 `启动体检台.bat`，或运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501
```

打开 [本地体检台](http://localhost:8501)，选择架构与优化等级，粘贴或上传 C 源码并点击「一键体检」。体检成功后，可在「优化建议」中填写 DeepSeek 配置并继续验证候选。API Key 仅保存在当前会话内存中。

关闭启动窗口或双击 `停止体检台.bat` 可停止服务。完整操作说明见 [操作手册](docs/操作手册.txt)。

## 目录与模块

```text
RISC-V-first/
├── app.py                       # Streamlit 界面
├── pipeline.py                  # 体检、优化、验证与报告编排
├── config.toml                  # 工具路径、超时与能耗权重
├── 启动体检台.bat / 停止体检台.bat
├── compiler/                    # 交叉编译、反汇编、裸机启动代码与链接脚本
├── profiler/                    # QEMU 运行、trace 解析、指标与能耗估算
├── optimizer/                   # R1～R9 规则、DeepSeek 接口、候选验证
├── reports/
│   ├── report.py                # Markdown 对比报告生成器
│   └── data/                    # 已整理的基准与演示数据
├── benchmarks/                  # 10 个 C 基准
│   ├── expected/                # 期望输出
│   ├── snapshots/               # 冻结指标快照
│   └── variants/                # 优化候选示例
├── tests/                       # 单元与真实工具链集成测试
│   └── fixtures/                # 固定镜像、trace 与环境样例
├── scripts/                     # 自检、端到端演示与数据整理脚本
├── docs/                        # 设计、使用、验证与参赛文档
│   ├── presentation/            # 演示文稿与制作资料
│   └── archive/                 # 早期实验归档
├── assets/                      # 图标等资源
├── workdir/                     # 本地任务原始证据，运行时生成
├── dist/offline/                # 本地离线安装资源，不入库
├── tools/                       # 本地工具链，不入库
└── .venv/                       # 本地 Python 环境，不入库
```

详细分类与维护约定见 [目录说明](docs/目录说明.md)。每次体检使用独立 `workdir/<task_id>/`，保留源码、ELF、`build.log`、`stdout.txt`、`disasm.txt`、`trace.log` 和指标 JSON；优化候选、验证结论和报告也保存在该任务目录下。

## 验证与常用脚本

运行全量测试（包含本机工具链与 QEMU 集成测试）：

```powershell
.\scripts\run_all_checks.bat
```

缺少外部工具时，相关测试会自动跳过；查看 pytest 汇总中的 `skipped`，区分离线测试通过与完整链路通过。其他常用命令：

```powershell
# 单元测试与回归用例
.\.venv\Scripts\python.exe -m pytest tests -q

# 编译、体检、快照、规则、优化候选与报告的端到端巡检
.\.venv\Scripts\python.exe -X utf8 scripts/e2e_check.py

# 生成演示所需的基准与对比数据
.\.venv\Scripts\python.exe scripts/collect_m7_data.py
```

`freeze_benchmarks.py` 会重写期望输出与冻结快照，仅在确认工具链或基准有意变更后使用。已有的快照、测试 trace、`reports/data/` 和任务证据均有用途，不能仅按 `.log`、`.json` 或 `.bin` 扩展名清理。

## 技术栈与适用边界

| 部分 | 实现 |
|---|---|
| 界面 | Streamlit、Plotly、pandas |
| 编排与测试 | Python 3.11+、pytest |
| 编译与静态分析 | xPack `riscv-none-elf-gcc`、`objdump`；自带 `rvcrt0.S` 和 `link.ld` |
| 运行与统计 | QEMU `virt` 裸机、semihosting、逐指令 trace 解析 |
| 优化 | 9 类规则、DeepSeek Chat Completions、jsonschema 输出校验 |

支持的目标为 `rv32i`、`rv64i`、`rv64gc`，优化等级为 `O0`、`O1`、`O2`、`O3`、`Os`。当前以单文件、可在裸机半主机环境中运行的 C 程序为主要对象。

- 指令数字来自真实编译、运行与日志解析；报告只读取落盘数据，LLM 不提供性能数字。
- 能耗分是「指令类别计数 × 可配置权重」的相对估算，不能替代硬件功耗测量。
- QEMU TCG 不模拟真实缓存与时序，宿主机耗时仅供参考，不能据此宣称真实芯片加速比。
- 输出一致性与确定性校验针对当前测试输入，不构成对所有输入的程序等价性证明。
- 本地 GCC、QEMU 和任务路径保持 ASCII；逐指令 trace 体积可能较大，历史任务应按证据用途管理。

## 文档导航

| 文档 | 内容 |
|---|---|
| [01-项目概述](docs/01-项目概述.md) | 背景、目标与项目边界 |
| [02-需求说明](docs/02-需求说明.md) | 用户故事、功能与验收标准 |
| [03-系统架构设计](docs/03-系统架构设计.md) | 模块、接口、数据模型与技术选型 |
| [04-环境搭建指南](docs/04-环境搭建指南.md) | Python、GCC 与 QEMU 安装和环境验证 |
| [05-开发路线图](docs/05-开发路线图.md) | 开发里程碑与历史达成记录 |
| [06-测试与验证方案](docs/06-测试与验证方案.md) | 数据口径、基准与验证闭环 |
| [07-演示方案](docs/07-演示方案.md) | 演示场景与检查项 |
| [08-风险与对策](docs/08-风险与对策.md) | 技术风险与降级方案 |
| [09-参赛技术文档](docs/09-参赛技术文档.md) | 技术说明与实测结果 |
| [10-演示视频分镜脚本](docs/10-演示视频分镜脚本.md) | 演示视频脚本 |
| [11-未来规划](docs/11-未来规划.md) | 后续方向与规划记录 |
| [操作手册](docs/操作手册.txt) | 日常启动、体检与优化操作 |
| [快速上手](docs/快速上手.md) | 环境安装与常见问题 |
| [目录说明](docs/目录说明.md) | 整理后的目录用途与维护约定 |

设计与路线图中包含历史规划；当前入口和支持范围以本 README 与实际代码为准。
