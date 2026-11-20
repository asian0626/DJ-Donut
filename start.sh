#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:$PATH"

supported_python() {
    "$1" -c 'import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 14)))' 2>/dev/null
}

if [ -x .venv/bin/python ]; then
    python_bin=.venv/bin/python
    if ! supported_python "$python_bin"; then
        echo "Unsupported .venv Python: use Python 3.10–3.14 (3.12 recommended)."
        echo "不支援目前 .venv 的 Python 版本，請使用 3.10–3.14（建議 3.12）。"
        echo "Keep your .env/data; rename the old .venv, then rerun with python3.12 installed."
        exit 1
    fi
else
    python_bin=""
    for candidate in python3.12 python3.14 python3.13 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && supported_python "$candidate"; then
            python_bin="$candidate"
            break
        fi
    done
    if [ -z "$python_bin" ]; then
        echo "Install Python 3.12 first / 請先安裝 Python 3.12 (macOS: brew install python@3.12)."
        exit 1
    fi
    "$python_bin" -m venv .venv
    python_bin=.venv/bin/python
    "$python_bin" -m pip install --upgrade pip
    "$python_bin" -m pip install -r requirements.txt
fi
"$python_bin" --version
if [ "${1:-}" = "--check" ]; then
    "$python_bin" -c 'import discord,yt_dlp; from settings import __version__; print("DJ Donut",__version__,"discord.py",discord.__version__,"yt-dlp",yt_dlp.version.__version__)'
    exit 0
fi
exec "$python_bin" main.py
