"""Pin catalog models to exact Hugging Face revisions, download them, and register them.

Run on the GPU host (it needs to reach huggingface.co once). For each model in
config/model-catalog.json it:
  1. resolves the current commit hash of the repo (or uses --revision KIND=sha),
  2. downloads that exact snapshot into the local HF cache (--download),
  3. registers kind/name/revision/licence in the Model Registry (status 'candidate').
  4. points the cache's `main` ref at the pinned commit (with --download).
The engines load models by repo name ("main"). With HF_HUB_OFFLINE=1 the Hub library
resolves "main" through refs/main in the local cache, so step 4 is what makes the
runtime load exactly the pinned snapshot and never contact Hugging Face.
`--check` verifies, before starting the engine, that every refs/main matches the
revision recorded in the Model Registry.

Env: REGISTRY_URL (default http://127.0.0.1:8083), REGISTRY_TOKEN, HF_TOKEN (optional).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def resolve_revision(repo: str) -> str:
    headers = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.environ.get("HF_TOKEN") else {}
    r = httpx.get(f"https://huggingface.co/api/models/{repo}/revision/main", headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()["sha"]


def pin_main_ref(repo: str, revision: str, cache_dir: Path | None = None) -> Path:
    """Make refs/main of the local HF cache resolve to `revision`; return the snapshot path."""
    from huggingface_hub import constants
    from huggingface_hub.file_download import repo_folder_name

    base = Path(cache_dir or constants.HF_HUB_CACHE) / repo_folder_name(repo_id=repo, repo_type="model")
    snapshot = base / "snapshots" / revision
    if not snapshot.is_dir():
        raise FileNotFoundError(f"snapshot {revision} of {repo} is not in the cache ({snapshot}); run with --download")
    refs = base / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    tmp = refs / "main.tmp"
    tmp.write_text(revision)
    tmp.replace(refs / "main")  # atomic: a concurrent reader sees the old or the new pin, never half
    return snapshot


def cached_main_ref(repo: str, cache_dir: Path | None = None) -> str | None:
    from huggingface_hub import constants
    from huggingface_hub.file_download import repo_folder_name

    ref = Path(cache_dir or constants.HF_HUB_CACHE) / repo_folder_name(repo_id=repo, repo_type="model") / "refs" / "main"
    return ref.read_text().strip() if ref.is_file() else None


def check_pins(catalog: list[dict], registry: str, token: str, kinds: set[str] | None) -> int:
    """Compare each model's cached refs/main with its newest registered revision."""
    data = httpx.get(f"{registry}/v1/models", headers={"Authorization": f"Bearer {token}"}, timeout=10).json()["data"]
    bad = 0
    for m in catalog:
        if kinds and m["kind"] not in kinds:
            continue
        registered = [r["revision"] for r in data if r["kind"] == m["kind"] and r["name"] == m["name"]]
        cached = cached_main_ref(m["hf_repo"])
        ok = bool(registered) and cached == registered[0]
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {m['kind']:<6} {m['hf_repo']}: cache main={cached} registry={registered[:1]}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=ROOT / "config" / "model-catalog.json")
    ap.add_argument("--only", help="comma list of kinds to process, e.g. stt,tts")
    ap.add_argument("--revision", action="append", default=[], help="KIND=sha to pin a specific revision")
    ap.add_argument("--download", action="store_true", help="download the pinned snapshot into the HF cache")
    ap.add_argument("--dry-run", action="store_true", help="resolve and print, register nothing")
    ap.add_argument("--check", action="store_true", help="verify cached refs/main == registered revision, then exit")
    args = ap.parse_args()

    pins = dict(item.split("=", 1) for item in args.revision)
    kinds = set(args.only.split(",")) if args.only else None
    registry = os.environ.get("REGISTRY_URL", "http://127.0.0.1:8083")
    token = os.environ.get("REGISTRY_TOKEN", "dev-console-to-registry")
    catalog = json.loads(args.catalog.read_text())["models"]
    if args.check:
        return check_pins(catalog, registry, token, kinds)
    failures = 0

    for m in catalog:
        if kinds and m["kind"] not in kinds:
            continue
        try:
            rev = pins.get(m["kind"]) or resolve_revision(m["hf_repo"])
            print(f"{m['kind']:<6} {m['hf_repo']} @ {rev}")
            if args.download:
                from huggingface_hub import snapshot_download
                snapshot_download(m["hf_repo"], revision=rev)
                print(f"       downloaded; refs/main pinned -> {pin_main_ref(m['hf_repo'], rev)}")
            if args.dry_run:
                continue
            notes = m.get("notes", "")
            if not m.get("licence_verified"):
                notes = ("LICENCE NOT YET VERIFIED BY A PERSON. " + notes).strip()
            r = httpx.post(f"{registry}/v1/models", headers={"Authorization": f"Bearer {token}"}, timeout=10, json={
                "kind": m["kind"], "name": m["name"], "revision": rev, "licence": m["licence"],
                "source_url": f"https://huggingface.co/{m['hf_repo']}", "notes": notes[:2000]})
            if r.status_code == 409:
                print("       already registered")
            else:
                r.raise_for_status()
                print(f"       registered id={r.json()['id']} status={r.json()['status']}")
        except Exception as exc:  # noqa: BLE001 - report each model, continue with the rest
            failures += 1
            print(f"       FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
