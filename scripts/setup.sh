#!/usr/bin/env bash
# 环境自检与安装脚本（跨平台，macOS / Linux / Windows-GitBash 可用）
# 幂等、无交互：已就绪的部分秒级跳过，缺什么装什么。
set -e
export PIP_DISABLE_PIP_VERSION_CHECK=1

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$SKILL_DIR/.venv"

# 1. 定位可用的 Python3
PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
  echo "[错误] 未找到 python3，请先安装 Python 3.10+（https://www.python.org/downloads/）" >&2
  exit 1
fi
echo "[1/4] Python: $($PY --version 2>&1)"

# 2. 创建 venv（已存在则跳过）
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[2/4] 创建独立虚拟环境 .venv ..."
  "$PY" -m venv "$VENV_DIR"
fi
PYBIN="$VENV_DIR/bin/python"
[ -x "$PYBIN" ] || PYBIN="$VENV_DIR/Scripts/python.exe"
if [ ! -x "$PYBIN" ]; then
  echo "[错误] 虚拟环境 python 创建失败" >&2
  exit 1
fi

# 3. 安装依赖（幂等：已装则跳过）
echo "[3/4] 检查依赖 playwright + pillow ..."
if "$PYBIN" -c "import playwright, PIL" 2>/dev/null; then
  echo "  ✓ 依赖已就绪，跳过安装"
else
  echo "  安装 playwright + pillow（首次约 50MB，请耐心等待）..."
  "$PYBIN" -m pip install --quiet --no-input --upgrade pip
  "$PYBIN" -m pip install --quiet --no-input playwright pillow
fi

# 4. 检测浏览器（优先系统 Chrome；不自动下载 chromium，避免误判/下载卡住）
echo "[4/4] 检查浏览器 ..."
HAS_CHROME=0
if [ -d "/Applications/Google Chrome.app" ] || [ -d "$HOME/Applications/Google Chrome.app" ]; then
  HAS_CHROME=1
elif command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; then
  HAS_CHROME=1
elif [ -f "/c/Program Files/Google/Chrome/Application/chrome.exe" ] || [ -f "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" ]; then
  HAS_CHROME=1
fi

if [ "$HAS_CHROME" = "1" ]; then
  echo "  ✓ 检测到系统 Chrome，浏览器就绪（fetch 脚本会直接使用，无需下载 chromium）"
else
  echo "  ⚠ 未检测到系统 Chrome。"
  echo "    建议安装 Google Chrome：https://www.google.com/chrome/"
  echo "    或手动下载 playwright chromium（可选，约 120MB）："
  echo "      $PYBIN -m playwright install chromium"
fi

echo ""
echo "✓ 环境就绪。运行："
echo "  $PYBIN $SKILL_DIR/scripts/fetch_note.py '<小红书笔记链接或ID>' --out <输出目录>"
