#!/usr/bin/env bash
# 环境自检与安装脚本（跨平台，macOS / Linux / Windows-GitBash 可用）
# 幂等、无交互：已就绪的部分秒级跳过，缺什么装什么。
set -eu
export PIP_DISABLE_PIP_VERSION_CHECK=1

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$SKILL_DIR/.venv"
REQ="$SKILL_DIR/requirements.txt"

# 1. 定位可用的 Python3（要求 3.9+）
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[错误] 未找到 python3，请先安装 Python 3.9+（https://www.python.org/downloads/）" >&2
  exit 1
fi
echo "[1/4] Python: $($PY --version 2>&1)"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "[错误] 当前 Python 版本过低，需要 3.9 及以上" >&2
  exit 1
fi

# 2. 创建 venv（已存在则跳过；Windows 的 python 在 Scripts/ 下）
if [ ! -x "$VENV_DIR/bin/python" ] && [ ! -x "$VENV_DIR/Scripts/python.exe" ]; then
  echo "[2/4] 创建独立虚拟环境 .venv ..."
  "$PY" -m venv "$VENV_DIR"
else
  echo "[2/4] .venv 已存在，跳过创建"
fi
PYBIN="$VENV_DIR/bin/python"
[ -x "$PYBIN" ] || PYBIN="$VENV_DIR/Scripts/python.exe"
if [ ! -x "$PYBIN" ]; then
  echo "[错误] 虚拟环境 python 创建失败" >&2
  exit 1
fi

# 3. 安装依赖（幂等：已装则跳过；优先用 requirements.txt 锁定大版本）
echo "[3/4] 检查依赖 playwright + pillow ..."
if "$PYBIN" -c "import playwright, PIL" 2>/dev/null; then
  echo "  ✓ 依赖已就绪，跳过安装"
else
  echo "  安装 playwright + pillow（首次约 50MB，请耐心等待）..."
  "$PYBIN" -m pip install --quiet --no-input --upgrade pip
  if [ -f "$REQ" ]; then
    "$PYBIN" -m pip install --quiet --no-input -r "$REQ"
  else
    "$PYBIN" -m pip install --quiet --no-input playwright pillow
  fi
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
  echo "    本脚本不会自动下载 chromium；如需回退方案，手动执行："
  echo "      $PYBIN -m playwright install chromium"
fi

echo ""
echo "✓ 环境就绪。运行："
echo "  $PYBIN $SKILL_DIR/scripts/fetch_note.py '<笔记链接 / 分享文案 / 笔记ID>' --out <输出目录>"
