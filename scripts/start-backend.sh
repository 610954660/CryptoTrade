#!/usr/bin/env bash
# macOS / Linux 一键启动后端
set -e
cd "$(dirname "$0")/../backend"

if [ ! -d ".venv" ]; then
  echo "[setup] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[install] 安装依赖 ..."
pip install -q -r requirements.txt

echo "[run] 启动 FastAPI (http://localhost:8000) ..."
exec python main.py
