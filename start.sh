#!/bin/bash
# Hermes Dashboard 启动脚本
# 自动检测 Python 环境并启动服务

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Try to find Python in various locations
PYTHON=""
for candidate in \
  "$HOME/.hermes/hermes-agent/.venv/bin/python3" \
  python3 \
  python; do
  if command -v "$candidate" &>/dev/null || [ -x "$candidate" ]; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "❌ 未找到 Python3，请先安装 Python 3.8+"
  exit 1
fi

echo "🐍 使用 Python: $PYTHON"
echo "🚀 启动 Hermes Dashboard API (端口 8650)..."
echo "   访问地址: http://localhost:8650"
echo ""

cd "$SCRIPT_DIR"
exec "$PYTHON" -m uvicorn dashboard_api:app --host 0.0.0.0 --port 8650 --reload
