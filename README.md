# wavecast — free text-to-speech reader (Piper)

A small web app that reads text aloud. Paste text or upload a `.txt`,
`.docx` or `.pdf`, then listen right away or download MP3s (one file,
separate parts, or a `.zip`).

It uses [Piper](https://github.com/OHF-Voice/piper1-gpl), an open-source
neural text-to-speech engine that runs **on the server's own CPU**. No
account, no API key, no external service — so nothing can block it, rate
limit it, or run out of quota — and it is small enough for Render's free plan.

## Features

- **Bilingual interface** (English / Portuguese-Brazil toggle, auto-detected
  from the browser).
- **Read aloud** or **Generate audio** (downloadable MP3).
- **Long text** is split into parts: download each part, all parts as a
  `.zip`, or one combined MP3.
- **File upload**: `.txt`, `.docx`, `.pdf` (text-based PDFs; scanned PDFs have
  no text to extract), up to 20 MB.
- **Speed control.** (Piper has no pitch control, so there is no pitch slider.)

## Voices

`download_voices.py` downloads the voices at build time (about 60 MB each):

| Voice | Language |
|---|---|
| Faber, Cadu | Portuguese (Brazil) |
| Lessac, Amy | English (US) |
| Alba | English (UK) |

The dropdown only lists voices that are actually installed. To change what is
installed, set `PIPER_VOICES` (comma separated) for the build, e.g.
`PIPER_VOICES=pt_BR-faber-medium,pt_BR-jeff-medium,es_ES-davefx-medium`.
Voices the app knows about are listed in `voices_catalog.py`; Piper has many
more at https://huggingface.co/rhasspy/piper-voices — add the name to
`CATALOG` to show it in the dropdown. Each installed voice uses disk space
and download time at build; only one voice is loaded in memory at a time.

## Run locally

Python 3.9+:

```bash
pip install -r requirements.txt
python download_voices.py
python app.py
```

Open http://127.0.0.1:5000

## Deploy on Render (free)

In your Render web service settings:

- **Build Command:** `pip install -r requirements.txt && python download_voices.py`
- **Start Command:**
  `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
  (Render uses the Start Command field; keep it at **one worker**, because
  jobs are tracked in memory.)
- You no longer need any `AZURE_*` or `EDGE_TTS_PROXY` environment variables.

Voices are downloaded during the build, so they are part of the deployment and
are not fetched again when the free service wakes up from sleep.

## Troubleshooting

Open **`/api/diagnose`** on the running site. It shows the installed voices and
runs a tiny test synthesis. If it says no voices are installed, the build
command did not run `download_voices.py`. Check the build log for lines
starting with `[voices]`.

## Notes

- Generated MP3s are kept in `output/` for an hour, then deleted automatically.
- Piper voices sound more synthetic than big cloud voices, but they are free,
  fast and private.
