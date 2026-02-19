import os
import uuid
import json
import logging
import threading
import subprocess
import base64
import shutil
import re
import difflib
import requests
from pathlib import Path
from flask import Flask, request, jsonify, render_template
from spellchecker import SpellChecker

# Suppress noisy Werkzeug request logs
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# One shared spell-checker instance (English only)
_spell = SpellChecker()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

# In-memory job store
jobs = {}

# ──────────────────────────────────────────────
# Ollama config
# ──────────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",    "video-spellcheck")

# Whisper model (lazy-loaded on first transcription)
_whisper_model = None


# ──────────────────────────────────────────────
# Helpers — frames
# ──────────────────────────────────────────────

def extract_frames(video_path: str, output_dir: str, fps: float = 0.5) -> list:
    """Extract one frame every 2 seconds from the video."""
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
        os.path.join(output_dir, "frame_%04d.jpg"),
        "-y", "-loglevel", "error",
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return sorted(Path(output_dir).glob("frame_*.jpg"))


# ── Echo detection: model returned prompt text instead of reading the image
_ECHO_MARKERS = [
    "spell-checker", "spell checker", "video frame", "json object",
    "no explanation", "no markdown", "reply with", "reply in json",
    "look at this", "lower thirds", "graphics, etc", "you are a",
    "on-screen text", "misspelled", "surrounding words",
]

def _is_echo(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return sum(1 for m in _ECHO_MARKERS if m in t) >= 2


def _validate_errors(errors: list) -> list:
    """For English: cross-check with pyspellchecker to remove false positives."""
    if not errors:
        return []
    confirmed = []
    for err in errors:
        word = re.sub(r"[^a-z]", "", err.get("word", "").lower())
        if not word:
            continue
        if _spell.unknown([word]):
            confirmed.append(err)
    return confirmed


def analyze_frame(frame_path: str, frame_index: int, language: str = "english", fps: float = 0.5) -> dict:
    """Send a single frame to Ollama and return on-screen text + spelling errors."""
    with open(frame_path, "rb") as f:
        image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    timestamp_sec = round(frame_index / fps)

    # Language-aware prompt
    if language == "hinglish":
        user_msg = (
            "What text is shown on screen? Are there any spelling errors? "
            "This video uses Hinglish — Hindi words written in Roman/English script. "
            "Words like 'kya', 'hai', 'nahi', 'bhai', 'yaar', 'aur', 'bhi', 'toh', 'matlab' "
            "are correctly spelled Hinglish and must NOT be flagged as errors."
        )
    else:
        user_msg = "What text is shown on screen? Are there any spelling errors?"

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
                    {"role": "user", "content": user_msg, "images": [image_b64]},
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

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    raw = m.group(0) if m else ""

    try:
        result = json.loads(raw)
    except Exception:
        result = {"text": None, "errors": []}

    if _is_echo(str(result.get("text") or "")):
        result = {"text": None, "errors": []}

    # English: cross-validate with dictionary. Hinglish: trust the model.
    if language == "english":
        result["errors"] = _validate_errors(result.get("errors", []))
    else:
        result["errors"] = result.get("errors", [])

    result["timestamp_sec"] = timestamp_sec
    result["frame_index"]   = frame_index
    return result


# ──────────────────────────────────────────────
# Helpers — audio / Whisper
# ──────────────────────────────────────────────

def _get_whisper_model():
    """Lazy-load faster-whisper model (downloads ~480 MB on first use)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    return _whisper_model


def extract_audio(video_path: str, audio_path: str) -> None:
    """Extract mono 16 kHz WAV from video using ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", audio_path, "-y", "-loglevel", "error"],
        capture_output=True, check=True,
    )


def transcribe_audio(audio_path: str, language: str) -> list:
    """Run Whisper on extracted audio. Returns list of {start, end, text} segments."""
    model = _get_whisper_model()
    whisper_lang = "hi" if language == "hinglish" else "en"
    segments, _ = model.transcribe(audio_path, language=whisper_lang, beam_size=3)
    result = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            result.append({"start": seg.start, "end": seg.end, "text": text})
    return result


# ──────────────────────────────────────────────
# Helpers — caption accuracy comparison
# ──────────────────────────────────────────────

def compare_captions(frame_results: list, audio_segments: list, language: str) -> list:
    """
    Compare what Whisper heard vs what the vision model saw on screen.
    For English: auto-flag mismatches using fuzzy matching.
    For Hinglish: always show side-by-side for human review (scripts differ).
    """
    rows = []
    for seg in audio_segments:
        start, end, spoken = seg["start"], seg["end"], seg["text"]
        if not spoken:
            continue

        # Find on-screen text from frames whose timestamp falls in this segment (±2s buffer)
        on_screen_parts = []
        for frame in frame_results:
            ft = frame.get("timestamp_sec", 0)
            if (start - 2) <= ft <= (end + 2):
                text = frame.get("text") or ""
                if text and text.lower() not in ("null", "none"):
                    on_screen_parts.append(text)

        on_screen = " | ".join(dict.fromkeys(on_screen_parts))  # deduplicate, preserve order
        ts = f"{int(start // 60)}:{int(start % 60):02d}"

        if language == "english":
            ratio = difflib.SequenceMatcher(None, spoken.lower(), on_screen.lower()).ratio() if on_screen else 0
            if on_screen:
                if ratio >= 0.55:
                    status = "match"
                elif ratio >= 0.25:
                    status = "partial"
                else:
                    status = "mismatch"
            else:
                status = "no_caption"  # speech but nothing on screen
            rows.append({
                "timestamp": ts,
                "spoken":    spoken,
                "on_screen": on_screen or "—",
                "status":    status,
                "score":     round(ratio * 100),
            })
        else:
            # Hinglish — always show for human review
            rows.append({
                "timestamp": ts,
                "spoken":    spoken,
                "on_screen": on_screen or "—",
                "status":    "review",
                "score":     None,
            })

    return rows


def format_timestamp(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


# ──────────────────────────────────────────────
# Background job
# ──────────────────────────────────────────────

def process_video(job_id: str, video_path: str, language: str):
    job = jobs[job_id]
    output_dir = f"/tmp/frames_{job_id}"
    audio_path = f"/tmp/audio_{job_id}.wav"

    try:
        job["status"] = "processing"

        # ── Step 1: Verify Ollama is reachable
        try:
            requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        except Exception:
            raise RuntimeError(
                f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
                "Make sure Ollama is running and the model is loaded."
            )

        # ── Step 2: Extract audio + transcribe
        job["progress"] = {"step": "Extracting audio from video…", "pct": 3, "phase": "audio"}
        extract_audio(video_path, audio_path)

        job["progress"] = {"step": "Transcribing audio with Whisper…", "pct": 7, "phase": "audio"}
        audio_segments = transcribe_audio(audio_path, language)

        # ── Step 3: Extract frames
        job["progress"] = {"step": "Extracting video frames…", "pct": 12, "phase": "frames"}
        frames = extract_frames(video_path, output_dir)

        if not frames:
            raise RuntimeError("Could not extract any frames. Is this a valid video file?")

        job["progress"] = {
            "step": f"Extracted {len(frames)} frames — starting AI analysis…",
            "pct": 15,
            "phase": "frames",
        }

        # ── Step 4: Analyse frames
        all_frames = []
        all_errors = []
        seen_words = set()

        for i, frame_path in enumerate(frames):
            pct = 15 + int((i / len(frames)) * 72)
            job["progress"] = {
                "step":         f"Analysing frame {i + 1} of {len(frames)}…",
                "pct":          pct,
                "frames_done":  i + 1,
                "total_frames": len(frames),
                "phase":        "analyse",
            }

            result = analyze_frame(str(frame_path), i + 1, language=language)
            all_frames.append(result)

            if result.get("errors"):
                for err in result["errors"]:
                    key = err.get("word", "").lower()
                    if key and key not in seen_words:
                        seen_words.add(key)
                        all_errors.append({
                            "word":          err["word"],
                            "suggestion":    err.get("suggestion", ""),
                            "context":       err.get("context", result.get("text", "")),
                            "timestamp_sec": result["timestamp_sec"],
                            "timestamp":     format_timestamp(result["timestamp_sec"]),
                        })

        # ── Step 5: Compare captions
        job["progress"] = {"step": "Comparing captions against audio…", "pct": 90, "phase": "compare"}
        caption_accuracy = compare_captions(all_frames, audio_segments, language)

        # ── Step 6: Done
        job["status"]   = "done"
        job["progress"] = {"step": "Analysis complete!", "pct": 100, "phase": "done"}
        job["results"]  = {
            "language":        language,
            "total_frames":    len(frames),
            "frames_with_text": sum(1 for f in all_frames if f.get("text")),
            "errors":          all_errors,
            "transcript": [
                {
                    "timestamp":     format_timestamp(f["timestamp_sec"]),
                    "timestamp_sec": f["timestamp_sec"],
                    "text":          f["text"],
                }
                for f in all_frames if f.get("text")
            ],
            "audio_transcript":  audio_segments,
            "caption_accuracy":  caption_accuracy,
        }

    except Exception as exc:
        job["status"] = "error"
        job["error"]  = str(exc)

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
        for path in (video_path, audio_path):
            try:
                os.remove(path)
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

    language  = request.form.get("language", "english").lower()
    job_id    = str(uuid.uuid4())
    video_path = f"/tmp/video_{job_id}"
    file.save(video_path)

    jobs[job_id] = {
        "status":   "queued",
        "progress": {"step": "Queued…", "pct": 0},
        "results":  None,
        "error":    None,
    }

    t = threading.Thread(target=process_video, args=(job_id, video_path, language), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found."}), 404
    job = jobs[job_id]
    return jsonify({
        "status":   job["status"],
        "progress": job["progress"],
        "results":  job.get("results"),
        "error":    job.get("error"),
    })


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🎬  Video Spell Checker")
    print(f"   Ollama:   {OLLAMA_BASE_URL}  (model: {OLLAMA_MODEL})")
    print(f"   Open:     http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
