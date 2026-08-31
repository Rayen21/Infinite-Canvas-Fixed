#!/bin/bash

# ============================================================
# ComfyUI-API-Modelscope 启动脚本
# Conda Environment: Infinite-Canvas
# Draw Things gRPC Server + FastAPI
# ============================================================

cd "$(dirname "$0")"

echo "============================================"
echo " ComfyUI-API-Modelscope (Conda Mode)"
echo "============================================"
echo ""

# ==================== 权限修复 ====================

echo "[1/5] 修复文件权限..."

xattr -r -d com.apple.quarantine *.command 2>/dev/null
xattr -r -d com.apple.quarantine main.py 2>/dev/null

chmod +x *.command 2>/dev/null
chmod +x main.py 2>/dev/null

echo "✓ 权限修复完成"
echo ""


# ==================== 清理 3000 端口 ====================

echo "[2/5] 检查 3000 端口..."

if lsof -i :3000 >/dev/null 2>&1; then
    echo "⚠ 发现 3000 端口占用，正在清理..."

    lsof -ti :3000 | xargs kill 2>/dev/null
    sleep 2

    if lsof -i :3000 >/dev/null 2>&1; then
        lsof -ti :3000 | xargs kill -9 2>/dev/null
        sleep 1
    fi

    echo "✓ 3000 端口已释放"
else
    echo "✓ 3000 端口可用"
fi

echo ""


# ==================== 清理 gRPC ====================

echo "[3/5] 检查 gRPC Server..."

GRPC_PORT=$(lsof -iTCP -sTCP:LISTEN -nP 2>/dev/null \
| grep -i grpc \
| awk '{print $9}' \
| cut -d: -f2 \
| head -1)

if [ -n "$GRPC_PORT" ]; then
    echo "⚠ gRPC 占用端口: $GRPC_PORT"
    echo "正在关闭旧进程..."

    lsof -ti :$GRPC_PORT | xargs kill -9 2>/dev/null
    sleep 1

    echo "✓ gRPC 端口已释放"
else
    echo "✓ 未发现 gRPC Server"
fi

echo ""


# ==================== Conda 环境 ====================

echo "[4/5] 激活 Conda 环境 Infinite-Canvas..."

if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniforge3/etc/profile.d/conda.sh"

elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"

elif [ -f "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh" ]; then
    source "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh"

else
    echo "✗ 找不到 Conda"
    exit 1
fi

conda activate Infinite-Canvas

if [ $? -ne 0 ]; then
    echo "✗ Conda 环境激活失败"
    exit 1
fi

echo "✓ 环境激活成功"

echo "Python:"
which python
python --version

echo ""


# ==================== gRPC Server ====================

echo "[5/5] 启动 Draw Things gRPC Server..."

MODEL_PATH="$HOME/Library/Containers/com.liuliu.draw-things/Data/Documents/Models"

if command -v gRPCServerCLI >/dev/null 2>&1; then

    gRPCServerCLI "$MODEL_PATH" --model-browser &

    GRPC_PID=$!

    sleep 3

    if kill -0 $GRPC_PID 2>/dev/null; then
        echo "✓ gRPC Server 启动成功 (PID: $GRPC_PID)"
    else
        echo "⚠ gRPC Server 启动失败"
    fi

else
    echo "⚠ 未找到 gRPCServerCLI，跳过"
fi

echo ""

# ==================== FastAPI ====================

echo "============================================"
echo "启动 ComfyUI-API-Modelscope"
echo "访问地址:"
echo "http://127.0.0.1:3000/"
echo "============================================"
echo ""

python main.py


echo ""
echo "============================================"
echo "服务已停止"
echo "============================================"

read -p "按 Enter 键退出..."