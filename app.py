import os
import uuid
import json
import logging
import threading
import subprocess
import base64
import shutil
import re
import requests
from pathlib import Path
from flask import Flask, request, jsonify, render_template
from spellchecker import SpellChecker

# Suppress noisy Werkzeug request logs (the 200 GET/POST lines)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# One shared spell-checker instance
_spell = SpellChecker()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB limit

# In-memory job store
jobs = {}

# ──────────────────────────────────────────────
# Ollama config  (override via environment vars)
# ──────────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",    "video-spellcheck")


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def extract_frames(video_path: str, output_dir: str, fps: float = 0.5) -> list[Path]:
    """Extract one frame every 2 seconds (fps=0.5) from the video."""
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
        os.path.join(output_dir, "frame_%04d.jpg"),
        "-y", "-loglevel", "error",
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return sorted(Path(output_dir).glob("frame_*.jpg"))


# ── Phrases that mean the model echoed our prompt back instead of reading the image
_ECHO_MARKERS = [
    "spell-checker", "spell checker", "video frame", "json object",
    "no explanation", "no markdown", "reply with", "reply in json",
    "look at this", "lower thirds", "graphics, etc", "you are a",
    "on-screen text", "misspelled", "surrounding words",
]

def _is_echo(text: str) -> bool:
    """Return True if the model returned the prompt instead of real caption text."""
    if not text:
        return False
    t = text.lower()
    return sum(1 for m in _ECHO_MARKERS if m in t) >= 2


def _validate_errors(errors: list) -> list:
    """
    Cross-check each error LLaVA flagged against pyspellchecker.
    Only keep words that a real dictionary also considers misspelled —
    this eliminates false positives like 'action' or 'though'.
    """
    if not errors:
        return []
    confirmed = []
    for err in errors:
        word = re.sub(r"[^a-z]", "", err.get("word", "").lower())
        if not word:
            continue
        if _spell.unknown([word]):        # pyspellchecker agrees it's wrong
            confirmed.append(err)
    return confirmed


def analyze_frame(frame_path: str, frame_index: int, fps: float = 0.5) -> dict:
    """Send a single frame to Ollama and get back on-screen text + confirmed spelling errors."""
    with open(frame_path, "rb") as f:
        image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    timestamp_sec = round(frame_index / fps)

    # JSON schema for structured output — forces the model to return valid JSON
    _format = {
        "type": "object",
        "properties": {
            "text":   {"type": ["string", "null"]},
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "word":       {"type": "string"},
                        "suggestion": {"type": "string"},
                    },
                    "required": ["word", "suggestion"],
                },
            },
        },
        "required": ["text", "errors"],
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": "What text is shown on screen? Are there any spelling errors?",
                        "images": [image_b64],
                    },
                ],
                "format": _format,
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()
        raw = response.json().get("message", {}).get("content", "").strip()
    except Exception:
        raw = ""

    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$",          "", raw)

    # Extract the first JSON object (model sometimes adds prose around it)
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    raw = m.group(0) if m else ""

    try:
        result = json.loads(raw)
    except Exception:
        result = {"text": None, "errors": []}

    # Discard result if the model echoed the prompt instead of reading the image
    if _is_echo(str(result.get("text") or "")):
        result = {"text": None, "errors": []}

    # Cross-validate errors with pyspellchecker to eliminate false positives
    result["errors"] = _validate_errors(result.get("errors", []))

    result["timestamp_sec"] = timestamp_sec
    result["frame_index"]   = frame_index
    return result


def format_timestamp(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


# ──────────────────────────────────────────────
# Background job
# ──────────────────────────────────────────────

def process_video(job_id: str, video_path: str):
    job = jobs[job_id]
    output_dir = f"/tmp/frames_{job_id}"

    try:
        job["status"]   = "processing"
        job["progress"] = {"step": "Extracting frames from video…", "pct": 5}

        # Verify Ollama is reachable before starting
        try:
            requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        except Exception:
            raise RuntimeError(
                f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
                "Make sure Ollama is running ('ollama serve') and the model is pulled."
            )

        frames = extract_frames(video_path, output_dir)

        if not frames:
            raise RuntimeError("Could not extract any frames. Is this a valid video file?")

        job["progress"] = {
            "step": f"Extracted {len(frames)} frames. Sending to Ollama ({OLLAMA_MODEL})…",
            "pct": 10,
        }

        all_frames  = []
        all_errors  = []
        seen_words  = set()

        for i, frame_path in enumerate(frames):
            pct = 10 + int((i / len(frames)) * 85)
            job["progress"] = {
                "step": f"Analysing frame {i + 1} of {len(frames)}…",
                "pct": pct,
                "frames_done": i + 1,
                "total_frames": len(frames),
            }

            result = analyze_frame(str(frame_path), i + 1)
            all_frames.append(result)

            if result.get("errors"):
                for err in result["errors"]:
                    key = err.get("word", "").lower()
                    if key and key not in seen_words:
                        seen_words.add(key)
                        all_errors.append(
                            {
                                "word":          err["word"],
                                "suggestion":    err.get("suggestion", ""),
                                "context":       err.get("context", result.get("text", "")),
                                "timestamp_sec": result["timestamp_sec"],
                                "timestamp":     format_timestamp(result["timestamp_sec"]),
                            }
                        )

        job["status"]   = "done"
        job["progress"] = {"step": "Analysis complete!", "pct": 100}
        job["results"]  = {
            "total_frames":     len(frames),
            "frames_with_text": sum(1 for f in all_frames if f.get("text")),
            "errors":           all_errors,
            "transcript": [
                {
                    "timestamp":     format_timestamp(f["timestamp_sec"]),
                    "timestamp_sec": f["timestamp_sec"],
                    "text":          f["text"],
                }
                for f in all_frames
                if f.get("text")
            ],
        }

    except Exception as exc:
        job["status"] = "error"
        job["error"]  = str(exc)

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
        try:
            os.remove(video_path)
        except OSError:
            pass


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify({"error": "No video file provided."}), 400

    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    job_id     = str(uuid.uuid4())
    video_path = f"/tmp/video_{job_id}"
    file.save(video_path)

    jobs[job_id] = {
        "status":   "queued",
        "progress": {"step": "Queued…", "pct": 0},
        "results":  None,
        "error":    None,
    }

    t = threading.Thread(target=process_video, args=(job_id, video_path), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found."}), 404
    job = jobs[job_id]
    return jsonify(
        {
            "status":   job["status"],
            "progress": job["progress"],
            "results":  job.get("results"),
            "error":    job.get("error"),
        }
    )


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🎬  Video Spell Checker")
    print(f"   Ollama:  {OLLAMA_BASE_URL}  (model: {OLLAMA_MODEL})")
    print(f"   Open:    http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
