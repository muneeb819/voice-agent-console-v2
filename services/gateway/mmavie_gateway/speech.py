"""Speech routes on the MMA AI Gateway (Phase 2).

  WS   /v1/stream/stt?model=<stt alias>   live transcription, relayed to the Speech Engine
  WS   /v1/stream/tts?model=<tts alias>   streaming synthesis with cancel (barge-in), relayed
  POST /v1/audio/speech                   one-shot TTS (OpenAI-compatible body, `model` = alias)

The Gateway does not inspect or transform audio. It authenticates the caller, picks a
healthy upstream replica for the alias, holds one of that replica's concurrency slots for
the life of the stream, and relays frames both ways unchanged. Failover happens only
before the stream is accepted; a stream that breaks mid-call is reported to the caller
(the Agent Runtime decides how to recover), never silently retried.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import websockets
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect

from mmavie_common.auth import authenticate_ws

log = logging.getLogger("mmavie.gateway.speech")

# Close codes used toward the caller (4000-4999 are application-defined).
CLOSE_UNAUTHENTICATED = 4401
CLOSE_UNKNOWN_ALIAS = 4404
CLOSE_TRY_AGAIN = 1013  # all upstreams busy or down


def _ws_base(url: str) -> str:
    return "ws" + url[4:] if url.startswith("http") else url


async def _relay(client: WebSocket, upstream) -> tuple[int, str]:
    """Pump frames both ways until either side ends. Returns (code, reason) for the caller."""

    async def client_to_upstream():
        while True:
            msg = await client.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            elif msg.get("text") is not None:
                await upstream.send(msg["text"])

    async def upstream_to_client():
        async for frame in upstream:
            if isinstance(frame, bytes):
                await client.send_bytes(frame)
            else:
                await client.send_text(frame)

    a = asyncio.create_task(client_to_upstream())
    b = asyncio.create_task(upstream_to_client())
    done, pending = await asyncio.wait({a, b}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in pending:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    for t in done:
        exc = t.exception()
        if exc and not isinstance(exc, (WebSocketDisconnect, websockets.ConnectionClosed)):
            log.warning("relay ended with %s", type(exc).__name__)
    code = upstream.close_code or 1000
    return code, (upstream.close_reason or "")[:120]


def register_speech_routes(app: FastAPI, *, settings, states: dict, failovers, forward_http, auth) -> None:
    async def proxy(ws: WebSocket, kind: str, path: str) -> None:
        caller = authenticate_ws(settings, ws.headers)
        if caller is None:
            await ws.close(code=CLOSE_UNAUTHENTICATED, reason="invalid or missing service token")
            return
        alias = ws.query_params.get("model", "")
        state = states.get(alias)
        if state is None or state.route.kind != kind:
            await ws.close(code=CLOSE_UNKNOWN_ALIAS, reason=f"unknown {kind} model alias")
            return

        for us in state.candidates():
            if us.sem.locked():
                continue
            await us.sem.acquire()
            try:
                upstream = await ws_connect(
                    _ws_base(us.up.url) + path,
                    additional_headers={**us.headers(), "x-mmavie-caller": caller},
                    open_timeout=settings.gateway_connect_timeout_s,
                    max_size=2**20,
                    ping_interval=20,
                    close_timeout=2,
                )
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                us.sem.release()
                us.healthy, us.detail = False, f"{type(exc).__name__} on stream open"
                failovers.labels(alias).inc()
                continue
            try:
                await ws.accept()
                code, reason = await _relay(ws, upstream)
                with contextlib.suppress(Exception):
                    await ws.close(code=code, reason=reason)
            finally:
                # The handler may be cancelled while closing (caller hung up); the slot must
                # be released whatever happens, or the replica slowly appears permanently full.
                try:
                    with contextlib.suppress(Exception):
                        await upstream.close()
                finally:
                    us.sem.release()
            return

        await ws.close(code=CLOSE_TRY_AGAIN, reason=f"no upstream available for '{alias}'")

    @app.websocket("/v1/stream/stt")
    async def stream_stt(ws: WebSocket):
        await proxy(ws, "stt", "/v1/stream/stt")

    @app.websocket("/v1/stream/tts")
    async def stream_tts(ws: WebSocket):
        await proxy(ws, "tts", "/v1/stream/tts")

    @app.post("/v1/audio/speech")
    async def speech(request: Request, caller: str = Depends(auth)):
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        alias = body.get("model") if isinstance(body, dict) else None
        state = states.get(alias) if isinstance(alias, str) else None
        if state is None or state.route.kind != "tts":
            raise HTTPException(404, f"unknown TTS model alias '{alias}'")
        return await forward_http(request, state, "/v1/audio/speech",
                                  {**body, "model": state.route.upstream_model}, caller, bool(body.get("stream")))
