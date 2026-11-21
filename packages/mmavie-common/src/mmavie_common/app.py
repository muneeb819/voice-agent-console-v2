"""FastAPI application factory shared by every MMA-VIE service.

Gives each service the same operational surface:
  GET /healthz  liveness (process is up)
  GET /readyz   readiness (all registered checks pass; 503 otherwise)
  GET /metrics  Prometheus metrics (keep private: scrape from inside the VPC only)
plus a request-id header, JSON logs and a request latency histogram.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from . import __version__
from .logging import configure_logging, request_id_var
from .settings import ServiceSettings

log = logging.getLogger("mmavie.app")


@dataclass
class CheckResult:
    ok: bool
    detail: str = ""


ReadinessCheck = Callable[[FastAPI], Awaitable[CheckResult]]
Hook = Callable[[FastAPI], Awaitable[None]]


def create_service_app(
    settings: ServiceSettings,
    *,
    title: str,
    readiness: dict[str, ReadinessCheck] | None = None,
    on_startup: list[Hook] | None = None,
    on_shutdown: list[Hook] | None = None,
    check_timeout_s: float = 2.0,
) -> FastAPI:
    settings.validate_for_start()
    configure_logging(settings.service_name, settings.log_level)
    checks = dict(readiness or {})
    registry = CollectorRegistry()
    req_latency = Histogram(
        "mmavie_http_request_seconds",
        "HTTP request latency",
        ["route", "method", "status"],
        registry=registry,
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    )
    req_errors = Counter("mmavie_http_errors_total", "HTTP 5xx responses", ["route"], registry=registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for hook in on_startup or []:
            await hook(app)
        log.info("service started", extra={"fields": {"version": __version__, "env": settings.environment}})
        try:
            yield
        finally:
            for hook in on_shutdown or []:
                try:
                    await hook(app)
                except Exception:  # noqa: BLE001 - shutdown must continue
                    log.exception("shutdown hook failed")

    app = FastAPI(title=title, version=__version__, lifespan=lifespan,
                  docs_url=None if settings.is_production_like else "/docs", redoc_url=None)
    app.state.settings = settings
    app.state.metrics_registry = registry

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            req_latency.labels(path, request.method, str(status)).observe(time.perf_counter() - start)
            if status >= 500:
                req_errors.labels(path).inc()
            request_id_var.reset(token)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok", "service": settings.service_name, "version": __version__}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        results: dict[str, dict] = {}

        async def run(name: str, fn: ReadinessCheck):
            try:
                res = await asyncio.wait_for(fn(app), check_timeout_s)
            except Exception as exc:  # noqa: BLE001 - any failure means not ready
                res = CheckResult(False, f"{type(exc).__name__}: {exc}"[:200])
            results[name] = {"ok": res.ok, "detail": res.detail}

        await asyncio.gather(*(run(n, f) for n, f in checks.items()))
        ready = all(r["ok"] for r in results.values())
        return JSONResponse({"ready": ready, "service": settings.service_name, "checks": results},
                            status_code=200 if ready else 503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return app
