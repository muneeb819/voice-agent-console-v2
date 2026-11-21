"""Runs against a real PostgreSQL. Set MMAVIE_TEST_DATABASE_URL (CI provides one); skipped otherwise."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mmavie_common.settings import ConfigError
from mmavie_registry.main import Settings, build_app

DB = os.environ.get("MMAVIE_TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[3]
AUTH = {"Authorization": "Bearer dev-console"}

needs_db = pytest.mark.skipif(not DB, reason="MMAVIE_TEST_DATABASE_URL not set")


def test_requires_database_url():
    with pytest.raises(ConfigError):
        build_app(Settings(environment="test", service_tokens="console:dev-console", database_url=""))


@pytest.fixture(scope="module")
def client():
    env = {**os.environ, "MMAVIE_DATABASE_URL": DB}
    subprocess.run([sys.executable, str(ROOT / "db" / "migrate.py")], check=True, env=env)
    app = build_app(Settings(environment="test", service_tokens="console:dev-console", database_url=DB))
    with TestClient(app) as c:
        yield c


@needs_db
def test_ready_with_migrated_schema(client):
    r = client.get("/readyz")
    assert r.status_code == 200, r.text


@needs_db
def test_register_requires_revision_and_licence(client):
    bad = {"kind": "llm", "name": "Qwen/Qwen3-8B", "revision": "x", "licence": "", "source_url": "https://huggingface.co/Qwen/Qwen3-8B"}
    assert client.post("/v1/models", headers=AUTH, json=bad).status_code == 422


@needs_db
def test_register_list_and_duplicate(client):
    m = {"kind": "tts", "name": "hexgrad/Kokoro-82M", "revision": "test-" + os.urandom(4).hex(),
         "licence": "Apache-2.0", "source_url": "https://huggingface.co/hexgrad/Kokoro-82M"}
    assert client.post("/v1/models", headers=AUTH, json=m).status_code == 201
    assert client.post("/v1/models", headers=AUTH, json=m).status_code == 409
    names = [x["revision"] for x in client.get("/v1/models?kind=tts", headers=AUTH).json()["data"]]
    assert m["revision"] in names


@needs_db
def test_migrations_are_idempotent():
    env = {**os.environ, "MMAVIE_DATABASE_URL": DB}
    out = subprocess.run([sys.executable, str(ROOT / "db" / "migrate.py"), "--status"], check=True, env=env,
                         capture_output=True, text=True).stdout
    assert "applied  0001_init.sql" in out and "pending" not in out
