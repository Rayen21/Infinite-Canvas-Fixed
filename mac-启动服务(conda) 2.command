#!/bin/bash
# 修复权限并启动服务 (Conda 环境版)
# 环境名称: Infinite-Canvas

cd "$(dirname "$0")"

echo "============================================"
echo "   ComfyUI-API-Modelscope (Conda Mode)"
echo "============================================\n"
echo "修复权限中..."

# 1. 移除安全限制并设置执行权限
xattr -r -d com.apple.quarantine *.command 2>/dev/null
xattr -r -d com.apple.quarantine main.py 2>/dev/null
chmod +x *.command 2>/dev/null
chmod +x main.py 2>/dev/null

echo "权限已修复！"
echo ""

# 2. 清理占用 3000 端口的旧进程，避免 address already in use
OLD_PID=$(lsof -ti :3000 2>/dev/null)
if [ -n "$OLD_PID" ]; then
   echo "检测到 3000 端口被占用，正在停止旧进程 (PID: $OLD_PID)..."
   kill $OLD_PID 2>/dev/null
   sleep 1
   if lsof -ti :3000 >/dev/null 2>&1; then
       kill -9 $(lsof -ti :3000) 2>/dev/null
   fi
   echo "旧进程已停止。"
   echo ""
fi

echo "正在尝试激活 Conda 环境 [Infinite-Canvas]..."

# 3. 寻找 Conda 初始化脚本并激活环境
# 我们尝试常见的 Miniforge/Miniconda 安装路径
CONDA_BASE_PATH=""

if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
   CONDA_BASE_PATH="$HOME/miniforge3/etc/profile.d/conda.sh"
elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
   CONDA_BASE_PATH="$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh" ]; then
   CONDA_BASE_PATH="/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh"
fi

if [ -n "$CONDA_BASE_PATH" ]; then
   # 关键步骤：加载 Conda 函数
   source "$CONDA_BASE_PATH"

   # 激活指定的虚拟环境
   conda activate "Infinite-Canvas"

   if [ $? -eq 0 ]; then
       echo "✅ 环境 [Infinite-Canvas] 激活成功！"
       echo ""
   else
       echo "❌ 错误：无法激活环境 [Infinite-Canvas]，请检查环境名称是否正确。"
       read -p "按 Enter 键退出..."
       exit 1
   fi
else
   echo "❌ 错误：找不到 Conda 安装路径，请检查 Miniforge 是否安装。"
   read -p "按 Enter 键退出..."
   exit 1
fi

echo "正在启动服务..."
echo "本机访问： http://127.0.0.1:3000/"
echo "============================================"
echo ""

# 4. 运行项目 (此时已经在 Conda 环境中了，直接用 python 即可)
python main.py