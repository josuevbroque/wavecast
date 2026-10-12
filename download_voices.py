"""Download the Piper voice files. Run during the Render build step:

    pip install -r requirements.txt && python download_voices.py

Voices already on disk are skipped. The build only fails if NO voice could be
downloaded, so one wrong voice name doesn't take the whole site down.
"""
import os
import sys
from pathlib import Path

from piper.download_voices import download_voice

from voices_catalog import DEFAULT_DOWNLOAD

VOICES_DIR = Path(os.environ.get("PIPER_VOICES_DIR") or Path(__file__).parent / "voices")


def main():
    wanted = [v.strip() for v in os.environ.get("PIPER_VOICES", "").split(",") if v.strip()]
    wanted = wanted or DEFAULT_DOWNLOAD
    VOICES_DIR.mkdir(parents=True, exist_ok=True)

    ok, failed = [], []
    for name in wanted:
        if (VOICES_DIR / f"{name}.onnx").exists() and (VOICES_DIR / f"{name}.onnx.json").exists():
            print(f"[voices] {name}: already present")
            ok.append(name)
            continue
        try:
            print(f"[voices] downloading {name} ...")
            download_voice(name, VOICES_DIR)
            ok.append(name)
        except Exception as exc:  # noqa: BLE001
            print(f"[voices] WARNING: could not download {name}: {exc}", file=sys.stderr)
            failed.append(name)

    print(f"[voices] ready: {ok}")
    if failed:
        print(f"[voices] failed: {failed}", file=sys.stderr)
    if not ok:
        print("[voices] ERROR: no voices could be downloaded.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
