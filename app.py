"""
Free AI Text-to-Speech web app.

Uses Microsoft Edge's built-in "Read Aloud" neural voices via the open-source
edge-tts library (https://github.com/rany2/edge-tts). No API key, no signup,
no billing - it talks to the same free service the Edge browser uses.

Two speech engines are supported:
  * Azure AI Speech (official, works from cloud hosts like Render). Used
    automatically when AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set.
  * Edge "Read Aloud" via edge-tts (free, unofficial). Used otherwise, but
    Microsoft often blocks it from cloud server IPs (HTTP 403).

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import asyncio
import io
from importlib import metadata as importlib_metadata
from xml.sax.saxutils import escape as xml_escape
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime

import aiohttp
import edge_tts
from docx import Document
from flask import Flask, jsonify, render_template, request, send_file
from pypdf import PdfReader

app = Flask(__name__)

# Reject uploads/requests larger than this (20 MB). Keeps a huge PDF from
# tying up the server or hitting the hosting platform's request timeout.
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": "That file is too large (limit is 20 MB)."}), 413

# Max characters sent to the TTS service in a single request. Edge's service
# can choke on extremely long single requests, so long text gets split into
# chunks (on paragraph/sentence boundaries) and stitched back together.
CHUNK_SIZE = 3000

# Microsoft's speech service sometimes rejects requests coming from cloud /
# datacenter IP ranges (Render, AWS, etc.) with HTTP 403. If that happens, you
# can route the speech traffic through a proxy by setting this environment
# variable, e.g.  EDGE_TTS_PROXY=http://user:pass@host:port
EDGE_TTS_PROXY = os.environ.get("EDGE_TTS_PROXY") or None

APP_VERSION = "2.0-azure"

# Azure AI Speech. Create a "Speech" resource in the Azure portal (the free
# F0 tier is enough), then set these two environment variables.
AZURE_KEY = (os.environ.get("AZURE_SPEECH_KEY") or "").strip()
AZURE_REGION = (os.environ.get("AZURE_SPEECH_REGION") or "").strip().lower()
USE_AZURE = bool(AZURE_KEY and AZURE_REGION)
ENGINE = "azure" if USE_AZURE else "edge"

AZURE_AUTH_MESSAGE = (
    "Azure Speech rejected the request (HTTP {status}). Check that "
    "AZURE_SPEECH_KEY is correct and that AZURE_SPEECH_REGION matches the "
    "region of your Speech resource (for example 'brazilsouth' or 'eastus'), "
    "and that the free monthly quota has not run out."
)


class AzureError(Exception):
    def __init__(self, status, detail=""):
        super().__init__(f"Azure Speech error {status}: {detail}".strip())
        self.status = status

BLOCKED_MESSAGE = (
    "Microsoft's speech service rejected the connection (HTTP 403). This "
    "usually means it is blocking this server's IP address, which is common "
    "on cloud hosts like Render. It works from most home connections. See "
    "the README for options (run locally, or set EDGE_TTS_PROXY)."
)

# A curated, friendly subset of voices instead of the full 300+ list.
# (Full list can be fetched from edge_tts.list_voices() if you want more.)
CURATED_VOICES = [
    {"id": "en-US-AndrewNeural", "label": "Andrew (US, Male)"},
    {"id": "en-US-AriaNeural", "label": "Aria (US, Female)"},
    {"id": "en-US-GuyNeural", "label": "Guy (US, Male)"},
    {"id": "en-US-JennyNeural", "label": "Jenny (US, Female)"},
    {"id": "en-US-EmmaNeural", "label": "Emma (US, Female)"},
    {"id": "en-GB-RyanNeural", "label": "Ryan (UK, Male)"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia (UK, Female)"},
    {"id": "en-AU-NatashaNeural", "label": "Natasha (Australia, Female)"},
    {"id": "en-AU-WilliamNeural", "label": "William (Australia, Male)"},
    {"id": "en-IN-NeerjaNeural", "label": "Neerja (India, Female)"},
    {"id": "es-ES-AlvaroNeural", "label": "Álvaro (Spanish, Male)"},
    {"id": "es-MX-DaliaNeural", "label": "Dalia (Spanish/Mexico, Female)"},
    {"id": "fr-FR-HenriNeural", "label": "Henri (French, Male)"},
    {"id": "fr-FR-DeniseNeural", "label": "Denise (French, Female)"},
    {"id": "de-DE-ConradNeural", "label": "Conrad (German, Male)"},
    {"id": "de-DE-KatjaNeural", "label": "Katja (German, Female)"},
    {"id": "it-IT-DiegoNeural", "label": "Diego (Italian, Male)"},
    {"id": "pt-BR-AntonioNeural", "label": "Antônio (Portuguese/Brazil, Male)"},
    {"id": "pt-BR-FranciscaNeural", "label": "Francisca (Portuguese/Brazil, Female)"},
    {"id": "ja-JP-KeitaNeural", "label": "Keita (Japanese, Male)"},
    {"id": "ja-JP-NanamiNeural", "label": "Nanami (Japanese, Female)"},
    {"id": "zh-CN-YunxiNeural", "label": "Yunxi (Chinese, Male)"},
    {"id": "zh-CN-XiaoxiaoNeural", "label": "Xiaoxiao (Chinese, Female)"},
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def split_text(text, max_len=CHUNK_SIZE):
    """Split text into chunks under max_len, breaking on paragraph/sentence
    boundaries where possible so audio doesn't cut off mid-sentence."""
    text = text.strip()
    if len(text) <= max_len:
        return [text]

    # First split on paragraphs, then sentences if a paragraph is still too long.
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
        # Paragraph itself is too long: split on sentences.
        sentences = re.split(r"(?<=[.!?])\s+", para)
        for sent in sentences:
            if len(current) + len(sent) + 1 <= max_len:
                current += (" " if current else "") + sent
            else:
                flush()
                current = sent
    flush()
    return chunks


async def synthesize_chunk_azure(text, voice, rate_str, pitch_str, out_path):
    """Synthesize one chunk with the Azure AI Speech REST API."""
    lang = "-".join(voice.split("-")[:2])  # "pt-BR-FranciscaNeural" -> "pt-BR"
    ssml = (
        f"<speak version='1.0' xml:lang='{lang}' "
        f"xmlns='http://www.w3.org/2001/10/synthesis'>"
        f"<voice name='{voice}'>"
        f"<prosody rate='{rate_str}' pitch='{pitch_str}'>{xml_escape(text)}</prosody>"
        f"</voice></speak>"
    )
    url = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "wavecast",
    }
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=ssml.encode("utf-8"), headers=headers) as resp:
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise AzureError(resp.status, detail)
            audio = await resp.read()
    with open(out_path, "wb") as f:
        f.write(audio)


async def synthesize_chunk(text, voice, rate, pitch, out_path):
    rate_str = f"{'+' if rate >= 0 else ''}{rate}%"
    pitch_str = f"{'+' if pitch >= 0 else ''}{pitch}Hz"
    if USE_AZURE:
        await synthesize_chunk_azure(text, voice, rate_str, pitch_str, out_path)
        return
    communicate = edge_tts.Communicate(
        text, voice, rate=rate_str, pitch=pitch_str, proxy=EDGE_TTS_PROXY
    )
    await communicate.save(out_path)


def is_blocked_error(exc):
    """True if the speech service refused the handshake with a 403.
    edge-tts already retries a 403 once itself (to correct for clock skew),
    so if one still reaches us it is not going to fix itself on retry."""
    return isinstance(exc, aiohttp.ClientResponseError) and exc.status == 403


async def synthesize_chunk_with_retry(text, voice, rate, pitch, out_path, attempts=3):
    """Try synthesizing a single chunk, retrying with a growing delay on
    transient failures (dropped connection, Azure 429 "too many requests").
    Returns (ok, error_message, fatal_code). fatal_code is "blocked" (Edge
    403) or "azure_auth" (bad key / region / quota); neither is retried
    because retrying cannot fix them."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            await synthesize_chunk(text, voice, rate, pitch, out_path)
            return True, None, None
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if is_blocked_error(exc):
                return False, str(exc), "blocked"
            if isinstance(exc, AzureError) and exc.status in (401, 403):
                return False, str(exc), "azure_auth"
            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)
    return False, str(last_error), None


def concat_mp3s(paths, out_path):
    """Concatenate MP3 files by simple byte concatenation.
    This works reliably for playback in virtually all players/browsers
    since consecutive MPEG audio frames don't require a shared container."""
    with open(out_path, "wb") as outfile:
        for p in paths:
            with open(p, "rb") as infile:
                outfile.write(infile.read())


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
    parts = []
    for para in document.paragraphs:
        parts.append(para.text)
    # Also pull text out of any tables, since paragraphs alone skip them.
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n\n".join(p for p in parts if p.strip())


def extract_text_from_pdf(file_storage):
    """Extract text from a text-based PDF. Scanned (image-only) PDFs have no
    text layer and will come back empty; that needs OCR, which this app
    doesn't do."""
    reader = PdfReader(file_storage.stream)
    if reader.is_encrypted:
        if not reader.decrypt(""):
            raise ValueError("This PDF is password-protected.")
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n\n".join(pages)
    # PDFs hard-wrap lines at the page width, which would make the voice
    # pause in the middle of sentences. Rejoin hyphenated words split across
    # lines, then turn single line breaks into spaces (blank lines stay as
    # paragraph breaks).
    text = re.sub(r"-\n(?=\w)", "", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text


@app.route("/")
def index():
    return render_template("index.html", voices=CURATED_VOICES, engine=ENGINE)


@app.route("/api/extract-text", methods=["POST"])
def extract_text():
    if "file" not in request.files:
        return jsonify({"error": "No file was uploaded."}), 400

    file_storage = request.files["file"]
    filename = file_storage.filename or ""
    ext = os.path.splitext(filename)[1].lower()

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



# In-memory job store. A job represents one "generate" request; it runs in a
# background thread so the HTTP request that starts it can return instantly,
# and the page polls /api/generate/status/<job_id> for progress. This avoids
# ever holding a single HTTP request open for the full duration of a long
# synthesis job, which is what was hitting the hosting platform's own proxy
# timeout (a limit separate from anything configurable in this app) and
# causing it to return an HTML error page instead of JSON.
JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_MAX_AGE_SECONDS = 3600  # stale jobs are dropped from memory after an hour


def _prune_old_jobs():
    cutoff = time.time() - JOB_MAX_AGE_SECONDS
    stale = [jid for jid, j in JOBS.items() if j.get("created", 0) < cutoff]
    for jid in stale:
        JOBS.pop(jid, None)


def run_generation_job(job_id, chunks, voice, rate, pitch, mode):
    work_dir = tempfile.mkdtemp(prefix=f"tts_{job_id}_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ordered_paths = [None] * len(chunks)
    fatal_code = None

    async def run():
        nonlocal fatal_code
        for i, chunk in enumerate(chunks):
            out_path = os.path.join(work_dir, f"part_{i:03d}.mp3")
            ok, error, fatal = await synthesize_chunk_with_retry(
                chunk, voice, rate, pitch, out_path
            )
            if ok:
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
            else:
                with JOBS_LOCK:
                    JOBS[job_id]["failed"].append({"index": i + 1, "error": error})
                # Blocked before a single part worked: every remaining part
                # would fail the same way, so stop instead of trying all of them.
                if fatal and not any(ordered_paths):
                    fatal_code = fatal
                    return

    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[job_id]["error"] = f"Speech generation failed: {exc}"
            JOBS[job_id]["status"] = "done"
        _cleanup_dir(work_dir)
        return

    succeeded_paths = [p for p in ordered_paths if p]

    with JOBS_LOCK:
        if not succeeded_paths:
            first_error = JOBS[job_id]["failed"][0]["error"] if JOBS[job_id]["failed"] else "Unknown error."
            if fatal_code == "blocked":
                JOBS[job_id]["error"] = BLOCKED_MESSAGE
                JOBS[job_id]["error_code"] = "blocked"
            elif fatal_code == "azure_auth":
                JOBS[job_id]["error"] = AZURE_AUTH_MESSAGE.format(status="401/403")
                JOBS[job_id]["error_code"] = "azure_auth"
            else:
                JOBS[job_id]["error"] = (
                    f"Speech generation failed for all {len(chunks)} part(s). First error: {first_error}"
                )
            JOBS[job_id]["status"] = "done"
        else:
            final = None
            if mode == "zip" and len(chunks) > 1:
                archive_name = f"speech_{stamp}_{job_id}.zip"
                archive_path = os.path.join(OUTPUT_DIR, archive_name)
                with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for idx, p in enumerate(ordered_paths):
                        if p:
                            zf.write(p, arcname=f"part_{idx + 1:02d}.mp3")
                final = {"download_url": f"/download/{archive_name}", "filename": archive_name}
            elif mode != "parts":
                if len(succeeded_paths) == 1:
                    # Only one chunk total: reuse its file directly as the final
                    # output rather than creating a needless duplicate copy.
                    only_part = JOBS[job_id]["parts"][0]
                    final = {
                        "download_url": only_part["download_url"],
                        "play_url": only_part["play_url"],
                        "filename": only_part["filename"],
                    }
                else:
                    final_name = f"speech_{stamp}_{job_id}.mp3"
                    final_path = os.path.join(OUTPUT_DIR, final_name)
                    concat_mp3s(succeeded_paths, final_path)
                    final = {
                        "download_url": f"/download/{final_name}",
                        "play_url": f"/play/{final_name}",
                        "filename": final_name,
                    }
            JOBS[job_id]["final"] = final
            JOBS[job_id]["status"] = "done"

    _cleanup_dir(work_dir)


def _cleanup_dir(work_dir):
    try:
        for f in os.listdir(work_dir):
            os.remove(os.path.join(work_dir, f))
        os.rmdir(work_dir)
    except OSError:
        pass


@app.route("/api/generate/start", methods=["POST"])
def generate_start():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    voice = data.get("voice") or "en-US-EmmaNeural"
    rate = int(data.get("rate", 0))
    pitch = int(data.get("pitch", 0))
    mode = data.get("mode", "single")
    if mode not in ("single", "zip", "parts"):
        mode = "single"

    if not text:
        return jsonify({"error": "No text provided."}), 400

    voice_ids = {v["id"] for v in CURATED_VOICES}
    if voice not in voice_ids:
        return jsonify({"error": "Unknown voice."}), 400

    rate = max(-50, min(50, rate))
    pitch = max(-50, min(50, pitch))

    chunks = split_text(text)
    job_id = uuid.uuid4().hex[:12]

    with JOBS_LOCK:
        _prune_old_jobs()
        JOBS[job_id] = {
            "status": "running",
            "mode": mode,
            "total_chunks": len(chunks),
            "parts": [],
            "failed": [],
            "final": None,
            "error": None,
            "error_code": None,
            "created": time.time(),
        }

    thread = threading.Thread(
        target=run_generation_job,
        args=(job_id, chunks, voice, rate, pitch, mode),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "total_chunks": len(chunks), "mode": mode})


@app.route("/api/generate/status/<job_id>")
def generate_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown or expired job."}), 404
        return jsonify({
            "status": job["status"],
            "mode": job["mode"],
            "total_chunks": job["total_chunks"],
            "parts": list(job["parts"]),
            "failed": list(job["failed"]),
            "final": job["final"],
            "error": job["error"],
            "error_code": job["error_code"],
        })


@app.route("/api/diagnose")
def diagnose():
    """Visit /api/diagnose to test the connection to Microsoft's speech
    service from wherever the app is running (handy on Render)."""
    try:
        version = importlib_metadata.version("edge-tts")
    except Exception:  # noqa: BLE001
        version = "unknown"

    async def probe():
        tmp = os.path.join(tempfile.gettempdir(), f"probe_{uuid.uuid4().hex[:8]}.mp3")
        try:
            await synthesize_chunk("hello", "en-US-EmmaNeural", 0, 0, tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    info = {
        "app_version": APP_VERSION,
        "engine": ENGINE,
        "azure_region": AZURE_REGION or None,
        "azure_key_set": bool(AZURE_KEY),
        "edge_tts_version": version,
        "proxy_configured": bool(EDGE_TTS_PROXY),
    }
    try:
        asyncio.run(probe())
        info.update(ok=True, message=f"Connected: the {ENGINE} speech engine works.")
        return jsonify(info)
    except Exception as exc:  # noqa: BLE001
        info.update(ok=False, error=str(exc))
        if is_blocked_error(exc):
            info["message"] = BLOCKED_MESSAGE
        elif isinstance(exc, AzureError) and exc.status in (401, 403):
            info["message"] = AZURE_AUTH_MESSAGE.format(status=exc.status)
        return jsonify(info), 502


@app.route("/download/<path:filename>")
def download(filename):
    safe_name = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.isfile(path):
        return "File not found.", 404
    return send_file(path, as_attachment=True, download_name=safe_name)


@app.route("/play/<path:filename>")
def play(filename):
    """Same as /download but without forcing a download, so it can be used
    directly as an <audio> element's source for immediate playback."""
    safe_name = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.isfile(path):
        return "File not found.", 404
    return send_file(path, as_attachment=False, mimetype="audio/mpeg")


if __name__ == "__main__":
    # Render (and most hosting platforms) provide the port to use via the
    # PORT environment variable, and expect the app to listen on 0.0.0.0
    # (all network interfaces) rather than just 127.0.0.1 (localhost only).
    # Locally, this still defaults to the familiar http://127.0.0.1:5000.
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
