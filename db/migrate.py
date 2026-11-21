"""Apply db/migrations/*.sql in order, exactly once each, each in its own transaction.

Usage:  MMAVIE_DATABASE_URL=postgresql://... python db/migrate.py [--status]

Migrations are forward-only. A migration that has been applied is never edited; changes
go in a new numbered file. The runner refuses to continue if an applied file's checksum
no longer matches what is on disk.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).parent / "migrations"
NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def discover() -> list[tuple[int, Path]]:
    found = []
    for p in sorted(MIGRATIONS.iterdir()):
        m = NAME.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise SystemExit(f"Migration numbers must be contiguous from 0001; found {versions}")
    return found


def main(argv: list[str]) -> int:
    url = os.environ.get("MMAVIE_DATABASE_URL")
    if not url:
        print("MMAVIE_DATABASE_URL is required", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # Serialise concurrent runners (e.g. two containers starting together).
        conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            applied = {v: s for v, s in conn.execute("SELECT version, sha256 FROM schema_migrations")}
            for version, path in discover():
                sql = path.read_text()
                digest = hashlib.sha256(sql.encode()).hexdigest()
                if version in applied:
                    if applied[version] != digest:
                        raise SystemExit(f"{path.name} changed after it was applied; add a new migration instead")
                    if "--status" in argv:
                        print(f"applied  {path.name}")
                    continue
                if "--status" in argv:
                    print(f"pending  {path.name}")
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations (version, name, sha256) VALUES (%s, %s, %s)",
                                 (version, path.name, digest))
                print(f"applied  {path.name}")
        finally:
            conn.execute("SELECT pg_advisory_unlock(727001)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
