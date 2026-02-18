# Changelog

All notable changes to Video Spell Checker are recorded here.

---

## [v0.5.0] — 2026-02-18

### Added
- **Video preview in results** — the video player now appears at the top of the results card so you can immediately confirm which video was checked without scrolling back up.
- **Estimated time remaining** — during the AI analysis phase, a live ETA ("~2m 30s remaining") is shown below the progress bar. Calculated from the per-frame processing rate and updated every poll. Shows "Calculating…" for the first few seconds while rate data is being collected.
- `frames_done` and `total_frames` added to the progress payload from the backend to power the ETA calculation.

---

## [v0.4.0] — 2026-02-18

### Added
- **Modelfile** — custom `video-spellcheck` Ollama model with the system instruction baked in as a `SYSTEM` block. The instruction is no longer sent with every API request, keeping requests smaller and cleaner.
- **Structured outputs** — Ollama `format` parameter now passes a full JSON schema to the model, forcing it to always return valid `{"text": ..., "errors": [...]}` JSON. Eliminates parsing failures and scene-description hallucinations.
- **Video preview** — after selecting a file, a video player appears in the upload card so you can confirm exactly which video you're checking before submitting.
- **Filename in results** — the results page now shows a file info bar with the video filename and size so there's no ambiguity about what was checked.
- **README.md** — project documentation covering setup, architecture, configuration, and troubleshooting.
- **CHANGELOG.md** — this file.

### Changed
- `run.sh` now builds the custom `video-spellcheck` model from the Modelfile instead of pulling `llama3.2-vision` directly.
- Default `OLLAMA_MODEL` changed from `llama3.2-vision` to `video-spellcheck`.
- Werkzeug HTTP request logs suppressed (set to ERROR level) — terminal output is now quiet during polling.

---

## [v0.3.0] — 2026-02-18

### Added
- Switched AI model from `llava` to `llama3.2-vision` for better vision accuracy.
- Split prompt into system message + simple user message to reduce echo behaviour.

### Changed
- Default port guidance updated to 8080 (avoids macOS AirPlay Receiver conflict on port 5000).

---

## [v0.2.0] — 2026-02-18

### Added
- **Ollama integration** — replaced Anthropic Claude API with local Ollama runtime (no API costs).
- `run.sh` launcher script — checks ffmpeg, Ollama, pulls model, installs deps, starts app.
- Echo detection (`_is_echo` function) — discards frames where the model returns the prompt text instead of image content.
- `pyspellchecker` cross-validation (`_validate_errors`) — eliminates false positives by confirming flagged words against a dictionary.

### Changed
- Initial model: `llava` (later upgraded in v0.3.0).
- Shortened AI prompt to reduce echo behaviour in llava.

### Fixed
- Port 5000 conflict with macOS AirPlay Receiver → use `PORT=8080`.
- Homebrew permissions error → `sudo chown -R $(whoami) /opt/homebrew`.

---

## [v0.1.0] — 2026-02-18

### Added
- Initial release — Flask web app for uploading videos and checking on-screen text for spelling errors.
- Drag & drop upload interface with 3-step progress bar.
- ffmpeg frame extraction at 0.5 fps (1 frame per 2 seconds).
- Background job processing with UUID tracking and client polling.
- Results table showing timestamp, misspelled word, suggestion, and context.
- Full on-screen text transcript (collapsible).
- Anthropic Claude Vision API as original backend.
- Dockerfile and Render.com deployment config.
- `deploy_setup.py` helper for GitHub push without git knowledge.
