@echo off
chcp 65001 >nul
title 停止 RV-Doctor 体检台

rem 双击即可停止正在运行的 RV-Doctor 网页服务。

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
    set FOUND=1
)
if %FOUND%==1 (
    echo RV-Doctor 体检台已停止。
) else (
    echo 当前没有正在运行的体检台。
)
timeout /t 2 /nobreak >nul
