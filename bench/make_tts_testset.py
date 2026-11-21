"""Build a synthetic STT test set by synthesising sentences with our own TTS (via the Gateway).

Output: bench/testsets/<name>/NNN.wav + manifest.jsonl ({"audio", "text"} per line).

What it is good for: a repeatable first STT latency baseline, and a TTS intelligibility
check (STT word errors on our own TTS output). What it is NOT: a measure of accuracy on real
callers. Clean synthetic speech is much easier than a phone line; real WER must come from
consented call recordings (see bench/README.md).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sentences", type=Path, default=ROOT / "bench" / "sentences" / "call_center_en.txt")
    ap.add_argument("--name", default="tts-roundtrip")
    ap.add_argument("--model", default="kokoro-82m")
    ap.add_argument("--voices", default="af_heart,am_michael", help="comma list; voices are rotated per sentence")
    args = ap.parse_args()

    gateway = os.environ.get("BENCH_GATEWAY_URL", "http://127.0.0.1:8080")
    token = os.environ.get("BENCH_TOKEN", "dev-console-to-gateway")
    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    sentences = [s.strip() for s in args.sentences.read_text().splitlines() if s.strip() and not s.startswith("#")]
    out = ROOT / "bench" / "testsets" / args.name
    out.mkdir(parents=True, exist_ok=True)

    lines = []
    with httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}"}) as c:
        for i, text in enumerate(sentences, 1):
            r = c.post(f"{gateway}/v1/audio/speech", json={
                "model": args.model, "input": text, "voice": voices[i % len(voices)],
                "response_format": "wav", "sample_rate": 16000})
            r.raise_for_status()
            wav = out / f"{i:03d}.wav"
            wav.write_bytes(r.content)
            lines.append(json.dumps({"audio": wav.name, "text": text, "voice": voices[i % len(voices)], "source": "tts"}))
    (out / "manifest.jsonl").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines)} items to {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
