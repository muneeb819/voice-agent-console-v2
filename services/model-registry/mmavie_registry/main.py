"""MMA Model Management — Phase 1 foundation.

Phase 1 delivers the registry's storage and the rule that no model is registered without
its revision and licence. Voice library, consent workflow, canary and rollback arrive in
Phase 5 on top of the same tables.
"""
from __future__ import annotations

from typing import Literal

import psycopg
from fastapi import Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from mmavie_common.app import CheckResult, create_service_app
from mmavie_common.auth import make_auth_dependency
from mmavie_common.settings import ConfigError, ServiceSettings

REQUIRED_SCHEMA_VERSION = 1


class Settings(ServiceSettings):
    service_name: str = "model-registry"
    database_url: str = ""


class ModelIn(BaseModel):
    kind: Literal["stt", "llm", "tts", "vad", "clone"]
    name: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=7, max_length=80, description="Exact upstream commit/revision hash")
    licence: str = Field(min_length=2, max_length=80)
    source_url: str = Field(pattern=r"^https://")
    weights_uri: str | None = Field(default=None, pattern=r"^s3://")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    notes: str = Field(default="", max_length=2000)


def build_app(settings: Settings | None = None):
    settings = settings or Settings()
    if not settings.database_url:
        raise ConfigError("MMAVIE_DATABASE_URL is required")

    async def connect():
        return await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row, connect_timeout=3)

    async def db_ready(_app) -> CheckResult:
        async with await connect() as conn:
            cur = await conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations")
            v = (await cur.fetchone())["v"]
        ok = v >= REQUIRED_SCHEMA_VERSION
        return CheckResult(ok, f"schema v{v}" + ("" if ok else f" < required v{REQUIRED_SCHEMA_VERSION}"))

    app = create_service_app(settings, title="MMA Model Management", readiness={"database": db_ready})
    auth = make_auth_dependency(settings)

    @app.get("/v1/models")
    async def list_models(kind: str | None = None, _: str = Depends(auth)):
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT id, kind, name, revision, licence, source_url, weights_uri, sha256, status, notes, created_at "
                "FROM models WHERE (%s::text IS NULL OR kind = %s) ORDER BY kind, name, created_at DESC", (kind, kind))
            return {"data": await cur.fetchall()}

    @app.post("/v1/models", status_code=201)
    async def register(model: ModelIn, caller: str = Depends(auth)):
        async with await connect() as conn:
            try:
                cur = await conn.execute(
                    "INSERT INTO models (kind, name, revision, licence, source_url, weights_uri, sha256, notes) VALUES "
                    "(%(kind)s, %(name)s, %(revision)s, %(licence)s, %(source_url)s, %(weights_uri)s, %(sha256)s, %(notes)s) "
                    "RETURNING id, status", model.model_dump())
            except psycopg.errors.UniqueViolation as exc:
                raise HTTPException(409, "this kind/name/revision is already registered") from exc
            row = await cur.fetchone()
            await conn.execute(
                "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s, 'model.register', %s, %s)",
                (caller, f"model:{row['id']}", psycopg.types.json.Jsonb(model.model_dump())))
            await conn.commit()
        return {"id": row["id"], "status": row["status"]}

    return app


def app_factory():
    return build_app()
