"""The pin must make an offline, repo-name load resolve to exactly the registered snapshot."""
import importlib.util
import sys
from pathlib import Path

import pytest

hub = pytest.importorskip("huggingface_hub")
spec = importlib.util.spec_from_file_location("register_models", Path(__file__).resolve().parents[1] / "register_models.py")
rm = importlib.util.module_from_spec(spec)
sys.modules["register_models"] = rm
spec.loader.exec_module(rm)

REPO = "hexgrad/Kokoro-82M"
PINNED, NEWER = "a" * 40, "b" * 40


def make_cache(tmp_path: Path) -> Path:
    base = tmp_path / "models--hexgrad--Kokoro-82M"
    for sha, marker in ((PINNED, "pinned"), (NEWER, "newer")):
        snap = base / "snapshots" / sha
        snap.mkdir(parents=True)
        (snap / "config.json").write_text(marker)
    (base / "refs").mkdir()
    (base / "refs" / "main").write_text(NEWER)  # someone else's later download moved main
    return tmp_path


def test_offline_load_by_repo_name_uses_pinned_snapshot(tmp_path, monkeypatch):
    cache = make_cache(tmp_path)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    before = hub.hf_hub_download(REPO, "config.json", cache_dir=cache, local_files_only=True)
    assert Path(before).read_text() == "newer"  # the problem Codex found: main != registry pin

    snapshot = rm.pin_main_ref(REPO, PINNED, cache_dir=cache)
    assert snapshot.name == PINNED and rm.cached_main_ref(REPO, cache_dir=cache) == PINNED
    after = hub.hf_hub_download(REPO, "config.json", cache_dir=cache, local_files_only=True)
    assert Path(after).read_text() == "pinned"


def test_pin_refuses_a_snapshot_that_was_never_downloaded(tmp_path):
    cache = make_cache(tmp_path)
    with pytest.raises(FileNotFoundError):
        rm.pin_main_ref(REPO, "c" * 40, cache_dir=cache)
    assert rm.cached_main_ref(REPO, cache_dir=cache) == NEWER  # unchanged on failure
