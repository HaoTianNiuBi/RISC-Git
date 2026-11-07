@echo off
chcp 65001 >nul
title RV-Doctor 环境安装
cd /d "%~dp0.."

echo ============================================================
echo   RV-Doctor 环境安装（拿到项目后运行一次即可）
echo   流程：检查 Python - 创建虚拟环境 - 安装依赖 - 就绪 RISC-V 工具链
echo   工具链优先从本包 dist\offline\tools.zip 离线解压；若没有 zip 则在线下载
echo ============================================================
echo.

rem ---- [1/3] Python ----
echo [1/3] 检查 Python ...
python --version >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未找到 Python。请先安装 Python 3.11 或更高版本：
    echo          https://www.python.org/downloads/
    echo          安装时务必勾选 "Add python.exe to PATH"，然后重新运行本脚本。
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo   已找到 %%v

if not exist ".venv\Scripts\python.exe" (
    echo   正在创建虚拟环境 .venv ...
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo   [错误] 虚拟环境创建失败，请确认 Python 安装完整后重试。
    pause
    exit /b 1
)

rem ---- [2/3] Python 依赖 ----
echo [2/3] 安装 Python 依赖（首次需联网，约 1-3 分钟）...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo   默认源安装失败，改用清华镜像重试 ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo   [错误] Python 依赖安装失败，请检查网络后重新运行本脚本。
        pause
        exit /b 1
    )
)
echo   依赖安装完成。

rem ---- [3/3] RISC-V 工具链 ----
echo [3/3] 检查 RISC-V 工具链 ...
if exist "tools\gcc\bin\riscv-none-elf-gcc.exe" if exist "tools\qemu\qemu-system-riscv64.exe" goto toolchain_ok

if exist "dist\offline\tools.zip" (
    echo   发现本包自带 dist\offline\tools.zip，正在解压到 tools\（约 2-3 分钟，需 2.2GB 磁盘空间）...
    if not exist "tools" mkdir "tools"
    tar -xf dist\offline\tools.zip -C tools
    if errorlevel 1 (
        echo   [错误] dist\offline\tools.zip 解压失败，请将压缩包内 gcc 和 qemu 解压到项目 tools 目录后重试。
        pause
        exit /b 1
    )
    goto toolchain_ok
)

echo   未找到 dist\offline\tools.zip，改为在线下载（需能访问 GitHub 与 qemu.weilnetz.de）...
if not exist "tools" mkdir "tools"
echo   - 下载 xPack RISC-V GCC 15.2.0-1（约 300MB）...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/download/v15.2.0-1/xpack-riscv-none-elf-gcc-15.2.0-1-win32-x64.zip' -OutFile 'tools\gcc.zip'"
if errorlevel 1 goto download_fail
echo   - 校验 SHA256 ...
powershell -NoProfile -Command "if ((Get-FileHash 'tools\gcc.zip' -Algorithm SHA256).Hash -ne '85EF714DACD273B1DADF4AF4892774520AC01915BFA6DA816A56E7E41591E09E') { exit 1 }"
if errorlevel 1 goto download_fail
if not exist "tools\_tmp" mkdir "tools\_tmp"
tar -xf tools\gcc.zip -C tools\_tmp
move "tools\_tmp\xpack-riscv-none-elf-gcc-15.2.0-1" "tools\gcc" >nul
rmdir /s /q "tools\_tmp"
del "tools\gcc.zip"
echo   - 下载 QEMU 11.1.0 Windows 安装包（约 197MB）...
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://qemu.weilnetz.de/w64/qemu-w64-setup-20260811.exe' -OutFile 'tools\qemu-setup.exe'"
if errorlevel 1 goto download_fail
echo   - 静默安装 QEMU 到 tools\qemu ...
"tools\qemu-setup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%cd%\tools\qemu"
del "tools\qemu-setup.exe"
goto toolchain_check

:download_fail
echo   [错误] 在线下载失败。解决办法任选其一：
echo     1. 换网络（或开代理）后重新运行本脚本；
echo     2. 手动向项目组索取 tools.zip，放到项目 dist\offline 目录后重新运行本脚本；
echo     3. 手动下载安装（详见 docs\快速上手.md 的 FAQ）：
echo        GCC : https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/tag/v15.2.0-1
echo        QEMU: https://qemu.weilnetz.de/w64/
pause
exit /b 1

:toolchain_check
if exist "tools\gcc\bin\riscv-none-elf-gcc.exe" if exist "tools\qemu\qemu-system-riscv64.exe" goto toolchain_ok
echo   [错误] 工具链未就绪，请按 docs\快速上手.md 的 FAQ 手动放入 tools\ 目录。
pause
exit /b 1

:toolchain_ok
echo   就绪检查：
"tools\gcc\bin\riscv-none-elf-gcc.exe" --version | findstr /i "gcc"
"tools\qemu\qemu-system-riscv64.exe" --version | findstr /i "qemu"
echo.
echo ============================================================
echo   安装完成！双击 启动体检台.bat 即可启动 RV-Doctor 体检台。
echo ============================================================
pause
