#!/bin/bash

# Exit on error
set -e

echo "========================================"
echo "  开始构建 PCIDS (程控安装部署系统)"
echo "========================================"

# 1. 确保安装了 pyinstaller
if ! command -v pyinstaller &> /dev/null; then
    echo "正在安装 pyinstaller..."
    python3 -m pip install pyinstaller
fi

# 2. 打包 Python 后端
echo ">>> 打包 Python 后端..."
cd backend

# Create backend entry script if it doesn't exist
cat << 'EOF' > run_backend.py
import uvicorn
from backend.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
EOF

# Determine executable extension based on OS
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    EXEC_NAME="pcids_backend.exe"
else
    EXEC_NAME="pcids_backend"
fi

# Build with pyinstaller
python3 -m PyInstaller --name "$EXEC_NAME" \
    --onefile \
    --clean \
    --add-data "../backend:backend" \
    run_backend.py

# Move the executable to a known location
mkdir -p dist
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    mv "dist/${EXEC_NAME}.exe" "dist/${EXEC_NAME}" 2>/dev/null || true
fi
cd ..

echo ">>> 后端打包完成: backend/dist/${EXEC_NAME}"

# 3. 编译前端与 Electron 脚本
echo ">>> 编译前端与 Electron..."
npm install
npm run build

# 4. 打包 Electron 客户端
echo ">>> 开始打包桌面客户端..."
echo "请选择打包平台:"
echo "1. 当前平台 (默认)"
echo "2. macOS (需要 macOS 环境)"
echo "3. Windows"
echo "4. Linux"
read -p "请输入选项 (1-4): " platform_choice

case $platform_choice in
    2)
        npm run package:mac
        ;;
    3)
        npm run package:win
        ;;
    4)
        npm run package:linux
        ;;
    *)
        npm run package
        ;;
esac

echo "========================================"
echo "  构建完成！输出文件位于 release/ 目录"
echo "========================================"
