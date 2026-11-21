"""MMA AI Gateway — the single internal entry point to all MMA-VIE inference.

Routes:
  * authenticated, OpenAI-compatible LLM routing (/v1/chat/completions, streaming and not)
  * alias -> upstream replicas from a validated route table (internal upstreams only)
  * active health probing, failover before the first byte, per-upstream load shedding
  * readiness = every route has at least one healthy upstream
  * speech: WebSocket STT/TTS relay and one-shot TTS (see speech.py, added in Phase 2)
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import Counter, Gauge

from mmavie_common.app import CheckResult, create_service_app
from mmavie_common.auth import make_auth_dependency
from mmavie_common.contracts.routing import Route, RouteTable, Upstream
from mmavie_common.settings import ConfigError, ServiceSettings

from .speech import register_speech_routes

log = logging.getLogger("mmavie.gateway")


class Settings(ServiceSettings):
    service_name: str = "gateway"
    gateway_routes_file: Path = Path("config/gateway.routes.json")
    gateway_probe_interval_s: float = 5.0
    gateway_upstream_timeout_s: float = 60.0
    gateway_connect_timeout_s: float = 2.0


@dataclass
class UpstreamState:
    up: Upstream
    healthy: bool = False
    detail: str = "not probed yet"
    sem: asyncio.Semaphore = field(init=False)

    def __post_init__(self):
        self.sem = asyncio.Semaphore(self.up.max_concurrency)

    def headers(self) -> dict[str, str]:
        if not self.up.token_env:
            return {}
        token = os.environ.get(self.up.token_env, "")
        return {"Authorization": f"Bearer {token}"} if token else {}


class RouteState:
    def __init__(self, route: Route):
        self.route = route
        self.upstreams = [UpstreamState(u) for u in route.upstreams]
        self._rr = itertools.count()

    def candidates(self) -> list[UpstreamState]:
        """Healthy upstreams in round-robin order, then unhealthy ones as a last resort."""
        n = len(self.upstreams)
        start = next(self._rr) % n
        ordered = self.upstreams[start:] + self.upstreams[:start]
        return [u for u in ordered if u.healthy] + [u for u in ordered if not u.healthy]


def load_routes(path: Path) -> RouteTable:
    if not path.is_file():
        raise ConfigError(f"Gateway route file not found: {path}")
    return RouteTable.model_validate(json.loads(path.read_text()))


def build_app(settings: Settings | None = None, *, routes: RouteTable | None = None,
              transport: httpx.AsyncBaseTransport | None = None):
    settings = settings or Settings()
    table = routes or load_routes(settings.gateway_routes_file)
    states = {r.alias: RouteState(r) for r in table.routes}

    async def probe_once(app) -> None:
        client: httpx.AsyncClient = app.state.http

        async def probe(us: UpstreamState):
            try:
                r = await client.get(f"{us.up.url}/readyz", headers=us.headers(), timeout=settings.gateway_connect_timeout_s)
                us.healthy, us.detail = r.status_code == 200, f"readyz {r.status_code}"
            except httpx.HTTPError as exc:
                us.healthy, us.detail = False, f"{type(exc).__name__}"
        await asyncio.gather(*(probe(u) for s in states.values() for u in s.upstreams))
        for alias, s in states.items():
            healthy_gauge.labels(alias).set(sum(u.healthy for u in s.upstreams))

    async def start(app):
        timeout = httpx.Timeout(settings.gateway_upstream_timeout_s, connect=settings.gateway_connect_timeout_s)
        app.state.http = httpx.AsyncClient(transport=transport, timeout=timeout)
        await probe_once(app)

        async def loop():
            while True:
                await asyncio.sleep(settings.gateway_probe_interval_s)
                try:
                    await probe_once(app)
                except Exception:  # noqa: BLE001 - probing must never stop
                    log.exception("probe loop error")

        app.state.prober = asyncio.create_task(loop())

    async def stop(app):
        app.state.prober.cancel()
        await app.state.http.aclose()

    async def routes_ready(_app) -> CheckResult:
        missing = [a for a, s in states.items() if not any(u.healthy for u in s.upstreams)]
        return CheckResult(not missing, f"no healthy upstream for {missing}" if missing else f"{len(states)} routes healthy")

    app = create_service_app(settings, title="MMA AI Gateway", readiness={"routes": routes_ready},
                             on_startup=[start], on_shutdown=[stop])
    app.state.routes = states
    reg = app.state.metrics_registry
    healthy_gauge = Gauge("mmavie_gateway_healthy_upstreams", "Healthy upstreams per alias", ["alias"], registry=reg)
    failovers = Counter("mmavie_gateway_upstream_failures_total", "Upstream attempts that failed", ["alias"], registry=reg)
    shed = Counter("mmavie_gateway_load_shed_total", "Requests rejected because all upstreams were busy", ["alias"], registry=reg)
    auth = make_auth_dependency(settings)

    @app.get("/v1/models")
    async def list_models(_: str = Depends(auth)):
        return {"object": "list", "data": [
            {"id": a, "object": "model", "owned_by": "mmavie", "kind": s.route.kind,
             "healthy_upstreams": sum(u.healthy for u in s.upstreams), "total_upstreams": len(s.upstreams)}
            for a, s in states.items()]}

    @app.get("/v1/routes/health")
    async def route_health(_: str = Depends(auth)):
        return {a: [{"url": u.up.url, "healthy": u.healthy, "detail": u.detail,
                     "in_flight": u.up.max_concurrency - u.sem._value} for u in s.upstreams]
                for a, s in states.items()}

    async def forward_http(request: Request, state: RouteState, path: str, upstream_body: dict,
                           caller: str, stream: bool) -> Response:
        """POST to a healthy upstream for the route; fail over before the first byte; shed when full."""
        alias = state.route.alias
        client: httpx.AsyncClient = request.app.state.http
        last_error = "no upstream available"
        all_busy = True
        for us in state.candidates():
            if us.sem.locked():
                continue
            all_busy = False
            await us.sem.acquire()
            try:
                req = client.build_request("POST", f"{us.up.url}{path}", json=upstream_body,
                                           headers={**us.headers(), "x-request-id": request.headers.get("x-request-id", ""),
                                                    "x-mmavie-caller": caller})
                resp = await client.send(req, stream=True)
            except httpx.HTTPError as exc:
                us.sem.release()
                us.healthy, us.detail = False, f"{type(exc).__name__} on request"
                failovers.labels(alias).inc()
                last_error = f"{type(exc).__name__}"
                continue
            if resp.status_code >= 500:
                await resp.aclose()
                us.sem.release()
                failovers.labels(alias).inc()
                last_error = f"upstream status {resp.status_code}"
                continue
            media_type = resp.headers.get("content-type", "application/octet-stream")
            if not stream:
                try:
                    content = await resp.aread()
                finally:
                    await resp.aclose()
                    us.sem.release()
                return Response(content, status_code=resp.status_code, media_type=media_type)

            async def relay(resp=resp, us=us):
                try:
                    async for chunk in resp.aiter_raw():
                        yield chunk
                finally:
                    await resp.aclose()
                    us.sem.release()

            return StreamingResponse(relay(), status_code=resp.status_code, media_type=media_type)

        if all_busy:
            shed.labels(alias).inc()
            return JSONResponse({"detail": f"all upstreams for '{alias}' are at capacity"}, status_code=503,
                                headers={"Retry-After": "1"})
        raise HTTPException(502, f"all upstreams for '{alias}' failed: {last_error}")

    @app.post("/v1/chat/completions")
    async def chat(request: Request, caller: str = Depends(auth)):
        try:
            body = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        alias = body.get("model") if isinstance(body, dict) else None
        state = states.get(alias) if isinstance(alias, str) else None
        if state is None or state.route.kind != "llm":
            raise HTTPException(404, f"unknown LLM model alias '{alias}'")
        return await forward_http(request, state, "/v1/chat/completions",
                                  {**body, "model": state.route.upstream_model}, caller, bool(body.get("stream")))

    register_speech_routes(app, settings=settings, states=states, failovers=failovers,
                           forward_http=forward_http, auth=auth)

    return app


def app_factory():
    return build_app()
