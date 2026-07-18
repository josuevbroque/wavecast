# wavecast — free AI text-to-speech reader

A small web app that reads text aloud using Microsoft Edge's free neural
voices (the same ones behind Edge's "Read aloud" feature). Paste text or
upload a `.txt`/`.docx` file, and either listen to it immediately or
download it as MP3 (as one file, as separate parts, or as a `.zip`).

It uses the open-source [`edge-tts`](https://github.com/rany2/edge-tts)
library, which talks directly to the free service Microsoft Edge uses. No
account, no API key, no billing.

## Features

- **Bilingual interface** — a toggle switches the whole UI between English
  and Portuguese (Brazil). English defaults to the Emma (US) voice;
  Portuguese defaults to Francisca (Brazil). You can still pick any other
  voice from the dropdown regardless of interface language.
- **Read aloud** — click to synthesize and play the audio immediately in
  the browser, no download needed.
- **Generate audio** — synthesizes and prepares a downloadable file. For
  long text that gets split into multiple parts, you can choose to:
  - download each part separately,
  - download all parts together as a `.zip`, or
  - combine the parts back into a single MP3.
- **File upload** — load text straight from a `.txt` or `.docx` file
  instead of pasting it in. `.docx` paragraph and table text is extracted;
  images and formatting are ignored since only the words matter for speech.
- **Speed and pitch controls**.

## Setup

You need **Python 3.9+** installed. Then, from this folder:

```bash
pip install -r requirements.txt
```

## Run it locally

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## How to use it

1. Paste or type your text into the box — or click **Upload .txt or .docx**
   to load text from a file.
2. Pick a voice (or use the language toggle's default), and adjust
   speed/pitch if you want.
3. Click **Read aloud** to hear it right away, or **Generate audio** to
   prepare a download.
4. If generating for download and your text is long enough to be split into
   parts, check the "split into separate parts" box to choose between
   separate files, a `.zip`, or one combined MP3. Leave it unchecked for a
   single merged MP3 by default.

Uploaded files are read on the server (nothing leaves the machine running
the app except the request to Microsoft's speech service once you generate
audio).

Long text (over ~3,000 characters) is automatically split into chunks so
the speech service doesn't choke on it: this happens regardless of which
download option you choose.

## Adding more voices

The app ships with ~23 common voices across several languages. Edge TTS
actually supports 300+ voices. To see the full list, run:

```bash
edge-tts --list-voices
```

Then add any `ShortName` you want to the `CURATED_VOICES` list near the top
of `app.py`.

## Deploying it publicly (e.g. on Render)

This app is set up to run on hosting platforms like [Render](https://render.com)
so it's usable from any device, not just localhost:

- **`Procfile`** tells the platform to run it with `gunicorn` (a
  production-grade server) instead of Flask's built-in dev server.
- **`app.py`** reads the `PORT` environment variable and binds to
  `0.0.0.0`, which is what hosting platforms expect — it still defaults to
  `127.0.0.1:5000` when run locally with no `PORT` set.

## Notes

- Generated MP3s are saved in the `output/` folder as well as served for
  download/playback — feel free to clear that folder out occasionally.
- Locally, this uses Flask's built-in development server by default, which
  is fine for personal use but isn't meant to be exposed to the internet
  directly — use the `gunicorn`/Render setup above for that instead.
