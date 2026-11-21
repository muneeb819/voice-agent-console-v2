"""Audio and scoring helpers for the MMA-VIE benchmarks (pure functions, unit-tested)."""
from __future__ import annotations

import re
import statistics
import unicodedata

import numpy as np

_BIAS, _CLIP = 0x84, 32635


def to_mono_float(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 2:
        a = a.mean(axis=1)
    return np.clip(a, -1.0, 1.0)


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio.astype(np.float32)
    import soxr

    return soxr.resample(audio.astype(np.float32), src_rate, dst_rate, quality="HQ").astype(np.float32)


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def float_to_ulaw(audio: np.ndarray) -> bytes:
    """ITU-T G.711 mu-law encoding (same table as Asterisk/VICIdial telephony)."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0)
    mag = np.minimum(np.abs(pcm), _CLIP) + _BIAS
    exponent = np.floor(np.log2(mag)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def ulaw_to_float(data: bytes) -> np.ndarray:
    u = ~np.frombuffer(data, dtype=np.uint8).astype(np.int32) & 0xFF
    sign, exponent, mantissa = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    mag = ((mantissa << 3) + _BIAS) << exponent
    return (np.where(sign, _BIAS - mag, mag - _BIAS) / 32768.0).astype(np.float32)


_PUNCT = re.compile(r"[^\w\s']")


def normalise(text: str) -> list[str]:
    """Lower-case, strip accents and punctuation, split on whitespace (WER normalisation)."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = _PUNCT.sub(" ", t.lower()).replace("'", "")
    return t.split()


def word_errors(reference: str, hypothesis: str) -> tuple[int, int]:
    """Return (edit distance in words, reference word count)."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1], len(ref)


def corpus_wer(pairs: list[tuple[str, str]]) -> float | None:
    errs = words = 0
    for ref, hyp in pairs:
        e, n = word_errors(ref, hyp)
        errs, words = errs + e, words + n
    return errs / words if words else None


def summarise(values: list[float], digits: int = 1) -> dict | None:
    """p50/p95/max/mean of a latency sample, or None when empty. p95 uses the inclusive method."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    p95 = statistics.quantiles(v, n=20, method="inclusive")[18] if len(v) > 1 else v[0]
    return {"n": len(v), "p50": round(statistics.median(v), digits), "p95": round(p95, digits),
            "max": round(v[-1], digits), "mean": round(statistics.fmean(v), digits)}
