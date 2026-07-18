# Wavecast — free AI text-to-speech reader

A small local web app that reads text aloud using Microsoft Edge's free neural
voices (the same ones behind Edge's "Read aloud" feature), and lets you
download the result as an MP3 — or as a `.zip` archive of MP3 parts for long
text.

It uses the open-source [`edge-tts`](https://github.com/rany2/edge-tts)
library, which talks directly to the free service Microsoft Edge uses. No
account, no API key, no billing.

**Important:** this only works with an actual internet connection on your own
machine (it needs to reach Microsoft's servers), and it's an unofficial,
reverse-engineered use of that service — not something Microsoft documents
or guarantees. It could stop working if Microsoft changes something on their
end, though the open-source project has reliably patched around such changes
in the past.

## Setup

You need **Python 3.9+** installed. Then, from this folder:

```bash
pip install -r requirements.txt
```

## Run it

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## How to use it

1. Paste or type your text into the box — or click **Upload .txt or .docx** to
   load text straight from a file.
2. Pick a voice, and adjust speed/pitch if you want.
3. Click **Generate audio**.
4. Click **Download** once it's ready.

Uploaded files are read on your own machine (nothing leaves your computer
except the request to Microsoft's speech service once you generate audio).
`.docx` files have their paragraph and table text extracted; images, headers/
footers, and formatting are ignored since only the words matter for speech.

Long text (over ~3,000 characters) is automatically split into chunks so the
speech service doesn't choke on it. By default the chunks are stitched back
into a single MP3. Check "download a .zip archive instead" if you'd rather
get each part as a separate file (handy for very long documents, e.g. one
file per few paragraphs).

## Adding more voices

The app ships with ~23 common voices across several languages. Edge TTS
actually supports 300+ voices. To see the full list, run:

```bash
edge-tts --list-voices
```

Then add any `ShortName` you want to the `CURATED_VOICES` list near the top
of `app.py`.

## Notes

- Generated MP3s are saved in the `output/` folder as well as served for
  download — feel free to clear that folder out occasionally.
- This uses Flask's built-in development server, which is fine for personal,
  local use but isn't meant to be exposed to the internet as-is.
