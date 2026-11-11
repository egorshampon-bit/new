#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "⚠️  Please configure your TELEGRAM_BOT_TOKEN in .env and run again."
fi

# Detect python executable
PYTHON_BIN="../esoteric_growth_engine/.venv/bin/python"
if [ ! -f "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    else
        echo "❌ Python binary not found!"
        exit 1
    fi
fi

mkdir -p data

echo "=================================================="
echo "🚀 Telegram Scheduler Bot (Отложка постов)"
echo "Using Python: $PYTHON_BIN"
echo "Working directory: $DIR"
echo "=================================================="

# Verification check
PYTHONPATH=. "$PYTHON_BIN" bot.py --check

echo "Starting bot polling and background scheduler worker..."
PYTHONPATH=. exec "$PYTHON_BIN" bot.py "$@"
