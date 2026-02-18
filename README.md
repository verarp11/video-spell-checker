# 🎬 Video Spell Checker

A local web app that scans video files for spelling errors in on-screen text — captions, lower thirds, subtitles, and graphic overlays. Powered by Ollama (no API costs, runs 100% on your Mac).

---

## How It Works

1. Upload a video via the browser interface (drag & drop or click to browse)
2. The app extracts one frame every 2 seconds using ffmpeg
3. Each frame is sent to a local AI model (llama3.2-vision via Ollama) which reads any on-screen text and flags spelling mistakes
4. Results are displayed in a report showing every error, suggested correction, and a full transcript of detected text

---

## Requirements

- **macOS** (Apple Silicon or Intel)
- **[Ollama](https://ollama.com)** — local AI runtime
- **ffmpeg** — video frame extraction (`brew install ffmpeg`)
- **Python 3.9+** — comes pre-installed on most Macs

---

## Quick Start

```bash
PORT=8080 bash ~/Downloads/video-spellcheck/run.sh
```

Then open **http://localhost:8080** in your browser.

> **Note:** Port 5000 is used by macOS AirPlay Receiver, so always use PORT=8080 (or any other free port).

The first run will:
- Check that ffmpeg and Ollama are installed
- Pull the `llama3.2-vision` base model (~7 GB, one-time download)
- Build the custom `video-spellcheck` model from the Modelfile
- Install Python dependencies
- Start the app

---

## Project Structure

```
video-spellcheck/
├── app.py              # Flask backend — frame extraction, AI analysis, job tracking
├── run.sh              # One-command local launcher
├── Modelfile           # Custom Ollama model with baked-in system instruction
├── requirements.txt    # Python dependencies
├── Dockerfile          # For cloud deployment (Render, etc.)
├── deploy_setup.py     # GitHub push helper (no git knowledge required)
├── CHANGELOG.md        # Version history
└── templates/
    └── index.html      # Frontend UI — drag & drop, progress bar, results
```

---

## Architecture

- **Backend**: Flask (Python) with threaded background job processing
- **AI Model**: Custom `video-spellcheck` model built on `llama3.2-vision` via Ollama
  - System instruction baked into the Modelfile (not sent with every request)
  - Structured JSON output enforced via Ollama's `format` parameter
- **Spell Validation**: `pyspellchecker` cross-checks AI-flagged words to eliminate false positives
- **Frame Extraction**: ffmpeg at 0.5 fps (1 frame per 2 seconds)
- **Job Tracking**: UUID-keyed in-memory dict with client polling every 1.5 seconds

---

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | Web server port |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `video-spellcheck` | Ollama model to use |

---

## Cloud Deployment (Render.com)

The app is also deployed at **https://video-spell-checker.onrender.com** (requires `ANTHROPIC_API_KEY` env var if using the cloud version).

For a fresh deploy, run:
```bash
python3 deploy_setup.py
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Port already in use | Run with `PORT=8080 bash run.sh` |
| ffmpeg not found | `brew install ffmpeg` |
| Ollama not found | Download from https://ollama.com |
| Homebrew permission error | `sudo chown -R $(whoami) /opt/homebrew` |
| Model echoing back the prompt | Already fixed — echo detection built into app.py |
