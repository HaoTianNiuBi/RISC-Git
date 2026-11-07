@echo off
rem ============================================================
rem RV-Doctor 一键自检（docs/05 M6 DoD：一键脚本全绿）
rem 内容：全量 pytest 回归（含工具链真实编译矩阵与 QEMU 集成用例）。
rem 前置：仓库根目录存在 .venv（docs/04 步骤一）；tools/ 下工具链就绪。
rem 用法：scripts\run_all_checks.bat
rem ============================================================
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [run_all_checks] FAIL: .venv missing. Run:
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
    exit /b 1
)

if not exist "tools\gcc\bin\riscv-none-elf-gcc.exe" (
    echo [run_all_checks] WARN: tools\gcc not ready - toolchain tests will skip
)
if not exist "tools\qemu\qemu-system-riscv64.exe" (
    echo [run_all_checks] WARN: tools\qemu not ready - qemu tests will skip
)

".venv\Scripts\python.exe" -m pytest tests -q
if errorlevel 1 (
    echo [run_all_checks] FAIL
    exit /b 1
)

echo [run_all_checks] ALL GREEN
endlocal
