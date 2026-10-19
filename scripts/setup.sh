#!/usr/bin/env bash
# 环境自检与安装脚本（跨平台，macOS / Linux / Windows-GitBash 可用）
# 在 skill 目录下建独立 venv，不污染系统 Python。
set -e

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$SKILL_DIR/.venv"

# 1. 定位可用的 Python3
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[错误] 未找到 python3，请先安装 Python 3.10+（https://www.python.org/downloads/）" >&2
  exit 1
fi
echo "[1/4] 使用 Python: $($PY --version 2>&1)"

# 2. 创建 venv
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[2/4] 创建独立虚拟环境 .venv ..."
  "$PY" -m venv "$VENV_DIR"
fi
PYBIN="$VENV_DIR/bin/python"
if [ ! -x "$PYBIN" ]; then
  # Windows GitBash 下 venv 的 python 在 Scripts/
  PYBIN="$VENV_DIR/Scripts/python.exe"
fi

# 3. 安装依赖
echo "[3/4] 安装依赖 playwright + pillow ..."
"$PYBIN" -m pip install --quiet --upgrade pip
"$PYBIN" -m pip install --quiet playwright pillow

# 4. 检测浏览器
echo "[4/4] 检测浏览器 ..."
HAS_CHROME=0
if [ -d "/Applications/Google Chrome.app" ] || [ -d "$HOME/Applications/Google Chrome.app" ]; then
  HAS_CHROME=1
elif command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1; then
  HAS_CHROME=1
elif [ -d "/Applications/Chromium.app" ] || [ -d "/Applications/Microsoft Edge.app" ]; then
  HAS_CHROME=1
fi

if [ "$HAS_CHROME" = "1" ]; then
  echo "  ✓ 检测到本机浏览器（将使用 channel='chrome' 或默认浏览器）"
else
  echo "  未检测到 Chrome，正在安装 playwright 自带的 chromium（约 120MB）..."
  "$PYBIN" -m playwright install chromium
fi

echo ""
echo "✓ 环境就绪。运行方式："
echo "  $VENV_DIR/bin/python $SKILL_DIR/scripts/fetch_note.py '<小红书笔记链接或ID>' --out <输出目录>"
