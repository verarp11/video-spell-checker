#!/bin/bash
# ──────────────────────────────────────────────────────
#  Video Spell Checker — Local Runner
#  Run this script to start the app on your Mac.
# ──────────────────────────────────────────────────────

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "🎬  Video Spell Checker"
echo "──────────────────────────────────────"

# 1. Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "❌  ffmpeg not found. Install it with:"
  echo "    brew install ffmpeg"
  exit 1
fi
echo "✅  ffmpeg found"

# 2. Check Ollama
if ! command -v ollama &>/dev/null; then
  echo "❌  Ollama not found. Download from https://ollama.com"
  exit 1
fi
echo "✅  Ollama found"

# 3. Make sure llava model is available
MODEL="${OLLAMA_MODEL:-llava}"
if ! ollama list | grep -q "$MODEL"; then
  echo "⏳  Pulling model '$MODEL' (first-time download, may take a few minutes)…"
  ollama pull "$MODEL"
fi
echo "✅  Model '$MODEL' ready"

# 4. Start Ollama in background if not already running
if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
  echo "⏳  Starting Ollama…"
  ollama serve &>/tmp/ollama.log &
  sleep 3
fi
echo "✅  Ollama running at http://localhost:11434"

# 5. Install Python dependencies
echo "⏳  Checking Python dependencies…"
pip3 install -q -r requirements.txt
echo "✅  Dependencies ready"

# 6. Start the app
PORT="${PORT:-5000}"
echo ""
echo "🚀  App running at http://localhost:$PORT"
echo "    Press Ctrl+C to stop."
echo ""

python3 app.py
