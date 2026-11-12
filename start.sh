#!/bin/bash
cd "$(dirname "$0")"

# 確保 Homebrew 路徑在 PATH 中 (ffmpeg)
export PATH="/opt/homebrew/bin:$PATH"

if [ ! -d ".venv" ]; then
    echo "❌ 找不到虛擬環境 .venv，正在建立..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

source .venv/bin/activate
python3 main.py
