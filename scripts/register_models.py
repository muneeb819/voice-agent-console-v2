"""Pin catalog models to exact Hugging Face revisions, download them, and register them.

Run on the GPU host (it needs to reach huggingface.co once). For each model in
config/model-catalog.json it:
  1. resolves the current commit hash of the repo (or uses --revision KIND=sha),
  2. downloads that exact snapshot into the local HF cache (--download),
  3. registers kind/name/revision/licence in the Model Registry (status 'candidate').
After this, the Speech Engine runs with HF_HUB_OFFLINE=1: it loads only the pinned,
already-downloaded weights and never contacts Hugging Face at runtime.

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog", type=Path, default=ROOT / "config" / "model-catalog.json")
    ap.add_argument("--only", help="comma list of kinds to process, e.g. stt,tts")
    ap.add_argument("--revision", action="append", default=[], help="KIND=sha to pin a specific revision")
    ap.add_argument("--download", action="store_true", help="download the pinned snapshot into the HF cache")
    ap.add_argument("--dry-run", action="store_true", help="resolve and print, register nothing")
    args = ap.parse_args()

    pins = dict(item.split("=", 1) for item in args.revision)
    kinds = set(args.only.split(",")) if args.only else None
    registry = os.environ.get("REGISTRY_URL", "http://127.0.0.1:8083")
    token = os.environ.get("REGISTRY_TOKEN", "dev-console-to-registry")
    catalog = json.loads(args.catalog.read_text())["models"]
    failures = 0

    for m in catalog:
        if kinds and m["kind"] not in kinds:
            continue
        try:
            rev = pins.get(m["kind"]) or resolve_revision(m["hf_repo"])
            print(f"{m['kind']:<6} {m['hf_repo']} @ {rev}")
            if args.download:
                from huggingface_hub import snapshot_download
                path = snapshot_download(m["hf_repo"], revision=rev)
                print(f"       downloaded to {path}")
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
