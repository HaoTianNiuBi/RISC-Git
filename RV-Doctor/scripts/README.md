# 脚本导航

从项目根目录运行 Python 脚本，使用 `.venv\Scripts\python.exe scripts/<脚本名>`。脚本按自身路径定位仓库；生成物统一进入 `workdir/`，数据维护脚本除外。

| 脚本 | 用途与产物 |
|---|---|
| `install.bat` | 首次安装；从 `dist/offline/tools.zip` 解压工具链，缺少离线包时尝试在线下载 |
| `run_all_checks.bat` | 全量 pytest 回归 |
| `e2e_check.py` | 基准、快照、规则、候选验证与报告的端到端巡检，输出至 `workdir/e2e/` |
| `m1_matrix.py` | 2 个程序 × 3 架构 × 5 优化等级编译矩阵，输出至 `workdir/m1_matrix/` |
| `m4_scan.py` | 10 个基准规则扫描，输出至 `workdir/m4_scan/` |
| `m5_demo.py` | 优化验证演示，输出至 `workdir/m5_demo/` |
| `m6_demo_reports.py` | 三个演示场景的实测报告，输出至 `workdir/m6_reports/` |
| `collect_m7_data.py` | 重新采集基准及对比数据，更新 `reports/data/` |
| `freeze_benchmarks.py` | 用独立参考实现核验结果并重写 `benchmarks/expected/`、`snapshots/` |
| `gen_smoke_elf.py` | 无 GCC 生成冒烟镜像，输出至 `workdir/smoke_images/`；不覆盖固定测试样例 |
| `make_smoke_metrics.py` | 运行固定冒烟样例并输出 `workdir/smoke64/metrics.json` |
| `make_icon.py` | 重建 `assets/rv-doctor.ico`，预览放 `workdir/assets/`；需 Pillow 与 NumPy |
| `make_shortcut.ps1` | 根据当前项目位置生成桌面快捷方式，核查记录放 `workdir/shortcuts/` |

`freeze_benchmarks.py` 和 `collect_m7_data.py` 会更新受版本控制的数据，只在需要重新核验或采集时运行。早期实验源码位于 `docs/archive/runtime-experiments/`，不参与正式流水线。
