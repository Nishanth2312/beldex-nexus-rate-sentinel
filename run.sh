#!/usr/bin/env bash
set -e

# Beldex Nexus Rate Sentinel Launcher (Linux & macOS)
cd "$(dirname "$0")"

echo "========================================================"
echo "    🚀 Starting Beldex Nexus Rate Sentinel"
echo "    Autonomous 30m Rate Difference & Selling Alert System"
echo "========================================================"

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is required but not installed."
    exit 1
fi

# Setup Virtual Environment if not exists
if [ ! -d "venv" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

# Run application or CLI command
if [ "$1" == "cli" ]; then
    shift
    exec ./venv/bin/python3 cli.py "$@"
elif [ "$1" == "status" ]; then
    exec ./venv/bin/python3 cli.py status
elif [ "$1" == "test" ]; then
    exec ./venv/bin/pytest -v
else
    PORT=${PORT:-8080}
    echo "🌐 Starting web dashboard on http://localhost:${PORT}"
    echo "📊 Open your browser to view live rates, 30m differences, and configure alerts."
    exec ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
fi
