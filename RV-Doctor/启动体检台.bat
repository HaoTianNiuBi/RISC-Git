@echo off
chcp 65001 >nul
title RV-Doctor 体检台
cd /d "%~dp0"

rem 双击即可启动 RV-Doctor 网页界面并自动打开浏览器。

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 .venv 虚拟环境。
    echo 请先阅读 docs\04-环境搭建指南.md 完成首次安装。
    pause
    exit /b 1
)

rem 已在运行则直接打开页面
netstat -ano | findstr ":8501" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo 检测到体检台已在运行，直接打开页面...
    start "" http://localhost:8501
    timeout /t 3 /nobreak >nul
    exit /b 0
)

echo 正在启动 RV-Doctor 体检台...
echo 启动后浏览器将自动打开 http://localhost:8501 （关闭本窗口即停止服务）
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start "" http://localhost:8501"
.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
pause
