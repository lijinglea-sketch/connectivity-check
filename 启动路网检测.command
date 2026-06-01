#!/bin/bash
# 路网连通性变更检测工具 — 一键启动
# 首次运行会自动安装依赖

cd "$(dirname "$0")"

echo "=========================================="
echo "  路网连通性变更检测工具"
echo "=========================================="

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误：未找到 python3"
    echo "请安装 Python 3.9+：https://www.python.org/downloads/"
    read -p "按回车键退出..."
    exit 1
fi

# 首次启动检查依赖
if [ ! -f ".deps_installed" ]; then
    echo "📦 首次启动，正在安装依赖（约 1-2 分钟）..."
    python3 -m pip install -r requirements.txt --user -q
    if [ $? -ne 0 ]; then
        echo "⚠️ 使用 --user 安装失败，尝试全局安装..."
        python3 -m pip install -r requirements.txt -q
    fi
    touch .deps_installed
    echo "✅ 依赖安装完成"
fi

# 启动 Streamlit
PORT=8504
open "http://localhost:${PORT}"

python3 -m streamlit run app.py \
    --server.port ${PORT} \
    --server.headless true \
    --browser.gatherUsageStats false

read -p "按回车键退出..."
