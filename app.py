"""
wavecast - free text-to-speech web app, powered by Piper.

Piper (https://github.com/OHF-Voice/piper1-gpl) is an open-source neural
text-to-speech engine that runs on the server's own CPU. No account, no API
key, no external service, so it can't be blocked or rate limited, and it is
small enough for Render's free plan.

Setup (also what Render's build command does):
    pip install -r requirements.txt
    python download_voices.py
Run locally:
    python app.py     ->  http://127.0.0.1:5000
"""

import gc
import io
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import wave
import zipfile
from datetime import datetime
from importlib import metadata as importlib_metadata

import lameenc
from docx import Document
from flask import Flask, jsonify, render_template, request, send_file
from pypdf import PdfReader

from piper import PiperVoice, SynthesisConfig
from voices_catalog import CATALOG

APP_VERSION = "3.0-piper"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = os.environ.get("PIPER_VOICES_DIR") or os.path.join(BASE_DIR, "voices")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)

# Reject uploads/requests larger than this (20 MB).
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": "That file is too large (limit is 20 MB)."}), 413


# Long text is split into chunks on paragraph/sentence boundaries. Each chunk
# becomes one "part"; parts give progress feedback and can be downloaded
# separately, zipped, or joined back into one MP3.
CHUNK_SIZE = 3000

MP3_BITRATE_KBPS = 64  # plenty for speech, keeps files small


# ---------------------------------------------------------------- voices ---

def available_voices():
    """Catalog voices whose model + config files are actually installed."""
    found = []
    for v in CATALOG:
        base = os.path.join(VOICES_DIR, v["id"])
        if os.path.isfile(base + ".onnx") and os.path.isfile(base + ".onnx.json"):
            found.append(v)
    return found


# Only one voice is kept in memory at a time (Render's free plan has 512 MB),
# and only one synthesis runs at a time.
_voice_lock = threading.Lock()
_loaded = {"id": None, "voice": None}


def get_voice(voice_id):
    with _voice_lock:
        if _loaded["id"] != voice_id:
            _loaded["voice"] = None
            _loaded["id"] = None
            gc.collect()
            path = os.path.join(VOICES_DIR, voice_id + ".onnx")
            _loaded["voice"] = PiperVoice.load(path, config_path=path + ".json")
            _loaded["id"] = voice_id
        return _loaded["voice"]


def rate_to_length_scale(rate):
    """Speed slider (-50..+50 %) -> Piper's length_scale (lower = faster)."""
    return round(1.0 / (1.0 + rate / 100.0), 3)


# ------------------------------------------------------------- synthesis ---

def split_text(text, max_len=CHUNK_SIZE):
    """Split text into chunks under max_len, breaking on paragraph/sentence
    boundaries where possible so audio doesn't cut off mid-sentence."""
    text = text.strip()
    if len(text) <= max_len:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""

    def flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_len:
            current += ("\n\n" if current else "") + para
            continue
        flush()
        if len(para) <= max_len:
            current = para
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para)
        for sent in sentences:
            # A single "sentence" longer than max_len (no punctuation): hard cut.
            while len(sent) > max_len:
                cut = sent.rfind(" ", 0, max_len)
                cut = cut if cut > 0 else max_len
                if current:
                    flush()
                chunks.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            if len(current) + len(sent) + 1 <= max_len:
                current += (" " if current else "") + sent
            else:
                flush()
                current = sent
    flush()
    return chunks


def wav_bytes_to_mp3(wav_buf):
    wav_buf.seek(0)
    with wave.open(wav_buf, "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    enc = lameenc.Encoder()
    enc.set_bit_rate(MP3_BITRATE_KBPS)
    enc.set_in_sample_rate(sample_rate)
    enc.set_channels(channels)
    enc.set_quality(2)
    return bytes(enc.encode(pcm)) + bytes(enc.flush())


def synthesize_chunk(text, voice_id, rate, out_path):
    """Synthesize one chunk of text straight to an MP3 file."""
    voice = get_voice(voice_id)
    config = SynthesisConfig(length_scale=rate_to_length_scale(rate))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        voice.synthesize_wav(text, wf, syn_config=config)
    mp3 = wav_bytes_to_mp3(buf)
    with open(out_path, "wb") as f:
        f.write(mp3)


def concat_mp3s(paths, out_path):
    """Join MP3 files by byte concatenation (plays fine in browsers/players)."""
    with open(out_path, "wb") as outfile:
        for p in paths:
            with open(p, "rb") as infile:
                outfile.write(infile.read())


# ------------------------------------------------------- file extraction ---

def extract_text_from_txt(file_storage):
    raw = file_storage.read()
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_text_from_docx(file_storage):
    document = Document(file_storage)
    parts = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n\n".join(p for p in parts if p.strip())


def extract_text_from_pdf(file_storage):
    """Text-based PDFs only; scanned (image-only) PDFs need OCR."""
    reader = PdfReader(file_storage.stream)
    if reader.is_encrypted:
        if not reader.decrypt(""):
            raise ValueError("This PDF is password-protected.")
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n\n".join(pages)
    text = re.sub(r"-\n(?=\w)", "", text)          # rejoin hyphenated words
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)   # hard-wrapped lines -> spaces
    return text


# ----------------------------------------------------------------- pages ---

@app.route("/")
def index():
    return render_template("index.html", voices=available_voices())


@app.route("/api/extract-text", methods=["POST"])
def extract_text():
    if "file" not in request.files:
        return jsonify({"error": "No file was uploaded."}), 400

    file_storage = request.files["file"]
    ext = os.path.splitext(file_storage.filename or "")[1].lower()

    try:
        if ext == ".txt":
            text = extract_text_from_txt(file_storage)
        elif ext == ".docx":
            text = extract_text_from_docx(file_storage)
        elif ext == ".pdf":
            text = extract_text_from_pdf(file_storage)
        else:
            return jsonify({"error": "Only .txt, .docx and .pdf files are supported."}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Couldn't read that file: {exc}"}), 400

    text = text.strip()
    if not text:
        return jsonify({"error": "No readable text was found in that file."}), 400
    return jsonify({"text": text})


# ------------------------------------------------------------------ jobs ---
# A job is one "generate" request. It runs in a background thread so the HTTP
# request returns instantly and the page polls for progress; this avoids the
# hosting platform's proxy timeout on long jobs.

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_MAX_AGE_SECONDS = 3600  # jobs and their files are dropped after an hour


def _prune_old_jobs():
    cutoff = time.time() - JOB_MAX_AGE_SECONDS
    for jid in [j for j, v in JOBS.items() if v.get("created", 0) < cutoff]:
        JOBS.pop(jid, None)
    try:
        for name in os.listdir(OUTPUT_DIR):
            path = os.path.join(OUTPUT_DIR, name)
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass


def run_generation_job(job_id, chunks, voice_id, rate, mode):
    work_dir = tempfile.mkdtemp(prefix=f"tts_{job_id}_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ordered_paths = [None] * len(chunks)

    try:
        for i, chunk in enumerate(chunks):
            out_path = os.path.join(work_dir, f"part_{i:03d}.mp3")
            try:
                synthesize_chunk(chunk, voice_id, rate, out_path)
            except Exception as exc:  # noqa: BLE001
                with JOBS_LOCK:
                    JOBS[job_id]["failed"].append({"index": i + 1, "error": str(exc)})
                continue
            part_name = f"speech_{job_id}_part{i + 1:02d}.mp3"
            part_final_path = os.path.join(OUTPUT_DIR, part_name)
            shutil.move(out_path, part_final_path)
            ordered_paths[i] = part_final_path
            with JOBS_LOCK:
                JOBS[job_id]["parts"].append({
                    "index": i + 1,
                    "filename": part_name,
                    "download_url": f"/download/{part_name}",
                    "play_url": f"/play/{part_name}",
                })

        succeeded = [p for p in ordered_paths if p]
        with JOBS_LOCK:
            job = JOBS[job_id]
            if not succeeded:
                first = job["failed"][0]["error"] if job["failed"] else "Unknown error."
                job["error"] = f"Speech generation failed for all {len(chunks)} part(s). First error: {first}"
            else:
                final = None
                if mode == "zip" and len(chunks) > 1:
                    archive_name = f"speech_{stamp}_{job_id}.zip"
                    with zipfile.ZipFile(os.path.join(OUTPUT_DIR, archive_name), "w", zipfile.ZIP_DEFLATED) as zf:
                        for idx, p in enumerate(ordered_paths):
                            if p:
                                zf.write(p, arcname=f"part_{idx + 1:02d}.mp3")
                    final = {"download_url": f"/download/{archive_name}", "filename": archive_name}
                elif mode != "parts":
                    if len(succeeded) == 1:
                        only = job["parts"][0]
                        final = {"download_url": only["download_url"],
                                 "play_url": only["play_url"],
                                 "filename": only["filename"]}
                    else:
                        final_name = f"speech_{stamp}_{job_id}.mp3"
                        concat_mp3s(succeeded, os.path.join(OUTPUT_DIR, final_name))
                        final = {"download_url": f"/download/{final_name}",
                                 "play_url": f"/play/{final_name}",
                                 "filename": final_name}
                job["final"] = final
            job["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[job_id]["error"] = f"Speech generation failed: {exc}"
            JOBS[job_id]["status"] = "done"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.route("/api/generate/start", methods=["POST"])
def generate_start():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    voices = available_voices()
    mode = data.get("mode", "single")
    if mode not in ("single", "zip", "parts"):
        mode = "single"

    if not text:
        return jsonify({"error": "No text provided."}), 400
    if not voices:
        return jsonify({
            "error": "No Piper voices are installed on this server. Run `python download_voices.py` "
                     "(on Render, add it to the Build Command).",
            "error_code": "no_voices",
        }), 503

    voice_id = data.get("voice") or voices[0]["id"]
    if voice_id not in {v["id"] for v in voices}:
        return jsonify({"error": "Unknown or uninstalled voice."}), 400

    try:
        rate = int(data.get("rate", 0))
    except (TypeError, ValueError):
        rate = 0
    rate = max(-50, min(50, rate))

    chunks = split_text(text)
    job_id = uuid.uuid4().hex[:12]

    with JOBS_LOCK:
        _prune_old_jobs()
        JOBS[job_id] = {
            "status": "running", "mode": mode, "total_chunks": len(chunks),
            "parts": [], "failed": [], "final": None,
            "error": None, "error_code": None, "created": time.time(),
        }

    threading.Thread(
        target=run_generation_job,
        args=(job_id, chunks, voice_id, rate, mode),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id, "total_chunks": len(chunks), "mode": mode})


@app.route("/api/generate/status/<job_id>")
def generate_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown or expired job."}), 404
        return jsonify({
            "status": job["status"], "mode": job["mode"],
            "total_chunks": job["total_chunks"],
            "parts": list(job["parts"]), "failed": list(job["failed"]),
            "final": job["final"], "error": job["error"],
            "error_code": job["error_code"],
        })


@app.route("/api/diagnose")
def diagnose():
    """Open /api/diagnose to see what is installed and whether synthesis works."""
    try:
        piper_version = importlib_metadata.version("piper-tts")
    except Exception:  # noqa: BLE001
        piper_version = "unknown"
    voices = available_voices()
    info = {
        "app_version": APP_VERSION,
        "piper_version": piper_version,
        "voices_dir": VOICES_DIR,
        "installed_voices": [v["id"] for v in voices],
    }
    if not voices:
        info.update(ok=False, message="No voices installed. Run download_voices.py.")
        return jsonify(info), 503
    tmp = os.path.join(tempfile.gettempdir(), f"probe_{uuid.uuid4().hex[:8]}.mp3")
    try:
        started = time.time()
        synthesize_chunk("Hello.", voices[0]["id"], 0, tmp)
        info.update(ok=True, test_voice=voices[0]["id"],
                    test_seconds=round(time.time() - started, 2),
                    message="Piper is working.")
        return jsonify(info)
    except Exception as exc:  # noqa: BLE001
        info.update(ok=False, error=str(exc))
        return jsonify(info), 500
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@app.route("/download/<path:filename>")
def download(filename):
    safe_name = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.isfile(path):
        return "File not found.", 404
    return send_file(path, as_attachment=True, download_name=safe_name)


@app.route("/play/<path:filename>")
def play(filename):
    safe_name = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.isfile(path):
        return "File not found.", 404
    return send_file(path, as_attachment=False, mimetype="audio/mpeg")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
