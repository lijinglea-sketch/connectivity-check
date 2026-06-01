@echo off
chcp 65001 >nul
REM 路网连通性变更检测工具 — Windows 一键启动
REM 首次运行会自动安装依赖

cd /d "%~dp0"

echo ==========================================
echo   路网连通性变更检测工具
echo ==========================================

REM 检查 Python3
python --version >nul 2>&1
if errorlevel 1 (
    python3 --version >nul 2>&1
    if errorlevel 1 (
        echo ❌ 错误：未找到 python 或 python3
        echo 请安装 Python 3.9+：https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set PYTHON=python3
) else (
    set PYTHON=python
)

REM 首次启动检查依赖
if not exist ".deps_installed" (
    echo 📦 首次启动，正在安装依赖（约 1-2 分钟）...
    %PYTHON% -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo ⚠️ 安装失败，尝试使用 --user 安装...
        %PYTHON% -m pip install -r requirements.txt --user -q
    )
    type nul > .deps_installed
    echo ✅ 依赖安装完成
)

REM 启动 Streamlit
set PORT=8504
start http://localhost:%PORT%

%PYTHON% -m streamlit run app.py ^
    --server.port %PORT% ^
    --server.headless true ^
    --browser.gatherUsageStats false

pause
