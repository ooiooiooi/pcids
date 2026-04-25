#!/bin/bash

# 程控安装部署系统 - 开发启动脚本

echo "========================================"
echo "  程控安装部署系统 (PCIDS)"
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误：未找到 Python3，请先安装 Python 3.10+"
    exit 1
fi

echo "Python 版本：$(python3 --version)"

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "错误：未找到 Node.js，请先安装 Node.js 18+"
    exit 1
fi

echo "Node.js 版本：$(node --version)"
echo ""

# 安装 Python 依赖
echo "正在安装 Python 依赖..."
pip3 install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "Python 依赖安装失败"
    exit 1
fi

echo ""
echo "========================================"
echo "  启动应用"
echo "========================================"
echo ""
echo "请选择启动方式:"
echo "1. 仅启动前端 (访问 http://localhost:5173)"
echo "2. 仅启动后端 (API 文档：http://127.0.0.1:8000/docs)"
echo "3. 同时启动前端和后端"
echo "4. 启动 Electron 桌面应用"
echo ""
read -p "请输入选项 (1-4): " choice

case $choice in
    1)
        echo "启动前端..."
        npm run dev:vite
        ;;
    2)
        echo "启动后端..."
        cd backend
        python3 -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
        ;;
    3)
        echo "同时启动前端和后端..."
        echo "前端：http://localhost:5173"
        echo "后端 API: http://127.0.0.1:8000/docs"
        echo ""

        # 启动后端
        cd backend
        python3 -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 &
        BACKEND_PID=$!

        # 等待后端启动
        sleep 3

        # 启动前端
        cd ..
        npm run dev:vite

        # 清理后端进程
        kill $BACKEND_PID 2>/dev/null
        ;;
    4)
        echo "启动 Electron 桌面应用..."
        npm run dev
        ;;
    *)
        echo "无效选项"
        exit 1
        ;;
esac
