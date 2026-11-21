import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

BENCH = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench_audio", BENCH / "audio.py")
audio = importlib.util.module_from_spec(spec)
sys.modules["bench_audio"] = audio
spec.loader.exec_module(audio)


def test_wer_basic_and_normalisation():
    assert audio.word_errors("Hello, World!", "hello world") == (0, 2)
    assert audio.word_errors("I'm on Part A", "im on part b") == (1, 4)
    assert audio.word_errors("one two three", "") == (3, 3)
    assert audio.corpus_wer([("a b c d", "a b c d"), ("a b", "a x")]) == pytest.approx(1 / 6)
    assert audio.corpus_wer([]) is None


def test_ulaw_round_trip_is_close():
    t = np.arange(8000) / 8000
    sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    back = audio.ulaw_to_float(audio.float_to_ulaw(sig))
    assert np.max(np.abs(back - sig)) < 0.02  # G.711 is logarithmic; ~1-2% error at this level
    assert audio.float_to_ulaw(np.zeros(4, np.float32)) == b"\xff" * 4  # u-law silence


def test_ulaw_matches_speech_engine_decoder():
    """Our encoder must produce what the Speech Engine decodes (same G.711 table)."""
    root = BENCH.parent / "services" / "speech"
    sys.path.insert(0, str(root))
    try:
        from app.audio import ulaw_to_float as engine_decode
    except Exception as exc:  # pragma: no cover - speech deps missing
        pytest.skip(f"speech engine not importable: {exc}")
    finally:
        sys.path.remove(str(root))
    sig = np.linspace(-0.9, 0.9, 2001, dtype=np.float32)
    ours = audio.ulaw_to_float(audio.float_to_ulaw(sig))
    theirs = engine_decode(audio.float_to_ulaw(sig))
    assert np.max(np.abs(ours - theirs)) < 1e-3


def test_summarise_percentiles():
    s = audio.summarise([100, 200, 300, 400, None])
    assert s["n"] == 4 and s["p50"] == 250 and s["max"] == 400
    assert audio.summarise([]) is None
