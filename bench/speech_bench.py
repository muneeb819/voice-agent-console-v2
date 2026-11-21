"""MMA-VIE speech benchmark: STT accuracy + latency and TTS latency, through the Gateway.

Measures exactly what the Phase 2 acceptance criteria name, on the real call path:
  STT  word error rate on a manifest of (audio, reference text), streamed in real time as
       8 kHz G.711 u-law (telephony) or 16 kHz PCM; and end-of-speech -> final transcript
       latency using the service's own endpointing (no client-side flush).
  TTS  time from 'speak' to first audio byte, total synthesis time, real-time factor.
  Both can run N concurrent streams to find where latency degrades.

Every run writes bench/results/<run_id>.json with the git commit, host, EC2 instance type
(when on EC2), Gateway model aliases, arguments, every per-item measurement and the summary.
A number is only quoted in a report together with its run_id.

Examples
  python bench/speech_bench.py stt --manifest bench/testsets/tts-roundtrip/manifest.jsonl --concurrency 1
  python bench/speech_bench.py tts --sentences bench/sentences/call_center_en.txt --concurrency 4
Env: BENCH_GATEWAY_URL (default http://127.0.0.1:8080), BENCH_TOKEN (Gateway service token).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import soundfile as sf
from websockets.asyncio.client import connect

sys.path.insert(0, str(Path(__file__).parent))
from audio import corpus_wer, float_to_pcm16, float_to_ulaw, resample, summarise, to_mono_float, word_errors  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = os.environ.get("BENCH_GATEWAY_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("BENCH_TOKEN", "dev-console-to-gateway")
FRAME_MS = 20


def ws_url(path: str) -> str:
    return GATEWAY.replace("http", "ws", 1) + path


def headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


# ------------------------------------------------------------------ run metadata
def run_metadata(args: argparse.Namespace) -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT,  # noqa: S607 - git from PATH is intended
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            return None

    def ec2_instance_type():
        import httpx
        try:
            tok = httpx.put("http://169.254.169.254/latest/api/token", timeout=0.5,
                            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}).text
            return httpx.get("http://169.254.169.254/latest/meta-data/instance-type", timeout=0.5,
                             headers={"X-aws-ec2-metadata-token": tok}).text
        except Exception:  # noqa: BLE001 - not on EC2
            return None

    def gateway_models():
        import httpx
        try:
            return httpx.get(f"{GATEWAY}/v1/models", headers=headers(), timeout=5).json()["data"]
        except Exception as exc:  # noqa: BLE001
            return f"unavailable: {type(exc).__name__}"

    return {
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6],
        "utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "ec2_instance_type": ec2_instance_type(),
        "gateway": GATEWAY,
        "gateway_models": gateway_models(),
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
    }


# ------------------------------------------------------------------ STT
def load_manifest(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            audio = Path(row["audio"])
            row["audio"] = str(audio if audio.is_absolute() else path.parent / audio)
            rows.append(row)
    if not rows:
        raise SystemExit(f"empty manifest: {path}")
    return rows


def encode_for_wire(wav_path: str, encoding: str) -> tuple[bytes, int, int, float]:
    """Return (payload, sample_rate, bytes_per_frame, speech_seconds)."""
    audio, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    audio = to_mono_float(audio)
    rate = 8000 if encoding == "ulaw" else 16000
    audio = resample(audio, sr, rate)
    payload = float_to_ulaw(audio) if encoding == "ulaw" else float_to_pcm16(audio)
    bytes_per_sample = 1 if encoding == "ulaw" else 2
    return payload, rate, rate * FRAME_MS // 1000 * bytes_per_sample, len(audio) / rate


async def stt_one(item: dict, alias: str, encoding: str, trailing_silence_s: float, timeout_s: float) -> dict:
    payload, rate, frame_bytes, seconds = encode_for_wire(item["audio"], encoding)
    silence_byte = b"\xff" if encoding == "ulaw" else b"\x00\x00"
    silence = silence_byte * int(rate * trailing_silence_s)
    finals: list[str] = []
    final_times: list[float] = []
    result = {"audio": item["audio"], "reference": item["text"], "audio_s": round(seconds, 2)}
    try:
        async with connect(ws_url(f"/v1/stream/stt?model={alias}"), additional_headers=headers(),
                           open_timeout=10, max_size=2**20) as ws:
            await ws.send(json.dumps({"sample_rate": rate, "encoding": encoding, "partials": True}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if ready.get("type") != "ready":
                raise RuntimeError(f"not ready: {ready}")

            async def reader():
                async for msg in ws:
                    if isinstance(msg, str):
                        m = json.loads(msg)
                        if m.get("type") == "final":
                            finals.append(m.get("text", ""))
                            final_times.append(time.perf_counter())
                        elif m.get("type") == "error":
                            raise RuntimeError(m.get("message"))

            read_task = asyncio.create_task(reader())
            # Stream in real time, as a phone call would arrive.
            start = time.perf_counter()
            stream = payload + silence
            speech_end_at = None
            for i in range(0, len(stream), frame_bytes):
                if speech_end_at is None and i >= len(payload):
                    speech_end_at = time.perf_counter()
                await ws.send(stream[i:i + frame_bytes])
                target = start + (i // frame_bytes + 1) * FRAME_MS / 1000
                await asyncio.sleep(max(0.0, target - time.perf_counter()))
            speech_end_at = speech_end_at or time.perf_counter()
            # Wait for the endpointed final that follows the end of speech.
            deadline = time.perf_counter() + timeout_s
            while time.perf_counter() < deadline and not any(t >= speech_end_at for t in final_times):
                if read_task.done():
                    read_task.result()
                    break
                await asyncio.sleep(0.01)
            await ws.send(json.dumps({"type": "close"}))
            with contextlib.suppress(Exception):
                await asyncio.wait_for(read_task, 5)
            after = [t for t in final_times if t >= speech_end_at]
            result["end_of_speech_to_final_ms"] = round(1000 * (after[0] - speech_end_at), 1) if after else None
    except Exception as exc:  # noqa: BLE001 - recorded per item, never hidden
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
    result["hypothesis"] = " ".join(f.strip() for f in finals).strip()
    errs, words = word_errors(item["text"], result["hypothesis"])
    result.update(word_errors=errs, reference_words=words)
    return result


async def run_stt(args) -> tuple[list[dict], dict]:
    items = load_manifest(args.manifest)
    queue: asyncio.Queue = asyncio.Queue()
    for _ in range(args.repeat):
        for it in items:
            queue.put_nowait(it)
    rows: list[dict] = []

    async def worker(wid: int):
        while not queue.empty():
            it = queue.get_nowait()
            row = await stt_one(it, args.model, args.encoding, args.trailing_silence, args.timeout)
            row["stream"] = wid
            rows.append(row)
            err = f"  ERROR {row['error']}" if "error" in row else ""
            print(f"  [{wid}] {Path(row['audio']).name}: final +{row.get('end_of_speech_to_final_ms')} ms, "
                  f"{row['word_errors']}/{row['reference_words']} word errors{err}")

    await asyncio.gather(*(worker(i) for i in range(args.concurrency)))
    ok = [r for r in rows if "error" not in r]
    summary = {
        "items": len(rows), "errors": len(rows) - len(ok),
        "wer": corpus_wer([(r["reference"], r["hypothesis"]) for r in ok]),
        "end_of_speech_to_final_ms": summarise([r["end_of_speech_to_final_ms"] for r in ok]),
        "missing_final": sum(1 for r in ok if r["end_of_speech_to_final_ms"] is None),
    }
    return rows, summary


# ------------------------------------------------------------------ TTS
async def tts_one(text: str, alias: str, voice: str, encoding: str, timeout_s: float) -> dict:
    row = {"text": text, "chars": len(text)}
    rate = 8000 if encoding == "ulaw" else 24000
    try:
        async with connect(ws_url(f"/v1/stream/tts?model={alias}"), additional_headers=headers(),
                           open_timeout=10, max_size=2**20) as ws:
            await ws.send(json.dumps({"type": "config", "voice": voice, "format": "ulaw" if encoding == "ulaw" else "pcm",
                                      "sample_rate": rate}))
            conf = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if conf.get("type") != "configured":
                raise RuntimeError(f"config rejected: {conf}")
            t0 = time.perf_counter()
            await ws.send(json.dumps({"type": "speak", "id": "b", "text": text}))
            first, total = None, 0
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout_s)
                if isinstance(msg, bytes):
                    first = first or time.perf_counter()
                    total += len(msg)
                else:
                    m = json.loads(msg)
                    if m.get("type") == "done":
                        break
                    if m.get("type") == "error":
                        raise RuntimeError(m.get("message"))
            done = time.perf_counter()
        audio_s = total / (1 if encoding == "ulaw" else 2) / rate
        row.update(first_audio_ms=round(1000 * (first - t0), 1) if first else None,
                   synth_ms=round(1000 * (done - t0), 1), audio_s=round(audio_s, 3),
                   rtf=round((done - t0) / audio_s, 3) if audio_s else None)
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return row


async def run_tts(args) -> tuple[list[dict], dict]:
    sentences = [s.strip() for s in args.sentences.read_text().splitlines() if s.strip() and not s.startswith("#")]
    queue: asyncio.Queue = asyncio.Queue()
    for _ in range(args.repeat):
        for s in sentences:
            queue.put_nowait(s)
    rows: list[dict] = []

    async def worker(wid: int):
        while not queue.empty():
            row = await tts_one(queue.get_nowait(), args.model, args.voice, args.encoding, args.timeout)
            row["stream"] = wid
            rows.append(row)
            print(f"  [{wid}] first audio {row.get('first_audio_ms')} ms, rtf {row.get('rtf')}"
                  f"{'  ERROR ' + row['error'] if 'error' in row else ''}")

    await asyncio.gather(*(worker(i) for i in range(args.concurrency)))
    ok = [r for r in rows if "error" not in r]
    summary = {"items": len(rows), "errors": len(rows) - len(ok),
               "first_audio_ms": summarise([r["first_audio_ms"] for r in ok]),
               "rtf": summarise([r["rtf"] for r in ok if r.get("rtf")], digits=3),
               "rtf_note": "synthesis time / audio duration; below 1.0 is faster than real time"}
    return rows, summary


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="kind", required=True)
    s = sub.add_parser("stt")
    s.add_argument("--manifest", type=Path, required=True)
    s.add_argument("--model", default="whisper-large-v3-turbo")
    s.add_argument("--encoding", choices=["ulaw", "pcm"], default="ulaw")
    s.add_argument("--trailing-silence", type=float, default=1.5)
    s.add_argument("--timeout", type=float, default=10.0)
    t = sub.add_parser("tts")
    t.add_argument("--sentences", type=Path, required=True)
    t.add_argument("--model", default="kokoro-82m")
    t.add_argument("--voice", default="af_heart")
    t.add_argument("--encoding", choices=["ulaw", "pcm"], default="ulaw")
    t.add_argument("--timeout", type=float, default=30.0)
    for p in (s, t):
        p.add_argument("--concurrency", type=int, default=1)
        p.add_argument("--repeat", type=int, default=1)
        p.add_argument("--label", default="", help="free text, e.g. 'g6e.xlarge, whisper fp16'")
        p.add_argument("--out", type=Path, default=ROOT / "bench" / "results")
    args = ap.parse_args()

    meta = run_metadata(args)
    print(f"run {meta['run_id']}  {args.kind}  concurrency={args.concurrency}  gateway={GATEWAY}")
    started = time.perf_counter()
    rows, summary = asyncio.run(run_stt(args) if args.kind == "stt" else run_tts(args))
    meta["wall_s"] = round(time.perf_counter() - started, 1)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{meta['run_id']}-{args.kind}.json"
    out.write_text(json.dumps({"meta": meta, "summary": summary, "items": rows}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"saved {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
