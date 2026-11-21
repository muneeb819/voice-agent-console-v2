"""Gateway speech relay against a real WebSocket upstream served by uvicorn on a local port."""
import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mmavie_common.contracts.routing import RouteTable
from mmavie_gateway.main import Settings, build_app

AUTH = {"Authorization": "Bearer dev-console"}
UPSTREAM_TOKEN = "dev-gw-to-speech"


def echo_speech_app() -> FastAPI:
    """Minimal stand-in for the Speech Engine: checks the bearer token, echoes frames."""
    app = FastAPI()

    def authorised(headers) -> bool:
        return headers.get("authorization") == f"Bearer {UPSTREAM_TOKEN}"

    @app.get("/readyz")
    async def ready():
        return {"ready": True}

    async def echo(ws: WebSocket, label: str):
        if not authorised(ws.headers):
            await ws.close(code=4401)
            return
        await ws.accept()
        await ws.send_json({"type": "ready", "kind": label})
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    await ws.send_bytes(msg["bytes"][::-1])
                elif msg["text"] == "close":
                    await ws.close(code=4000, reason="upstream done")
                    return
                else:
                    await ws.send_text("echo:" + msg["text"])
        except WebSocketDisconnect:
            return

    @app.websocket("/v1/stream/stt")
    async def stt(ws: WebSocket):
        await echo(ws, "stt")

    @app.websocket("/v1/stream/tts")
    async def tts(ws: WebSocket):
        await echo(ws, "tts")

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        body = await request.json()
        return Response(f"RIFF:{body['model']}:{body['input']}".encode(), media_type="audio/wav")

    return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def upstream_url():
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(echo_speech_app(), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


def gateway(urls: list[str], max_concurrency: int = 4):
    os.environ["SPEECH_TOKEN"] = UPSTREAM_TOKEN
    ups = [{"url": u, "token_env": "SPEECH_TOKEN", "max_concurrency": max_concurrency} for u in urls]
    routes = RouteTable(routes=[
        {"alias": "whisper-test", "kind": "stt", "upstream_model": "large-v3-turbo", "upstreams": ups},
        {"alias": "kokoro-test", "kind": "tts", "upstream_model": "kokoro", "upstreams": ups},
    ])
    settings = Settings(environment="test", service_tokens="runtime:dev-console", gateway_probe_interval_s=3600)
    return build_app(settings, routes=routes)


def test_stt_stream_relays_text_and_binary_both_ways(upstream_url):
    with TestClient(gateway([upstream_url])) as c:
        with c.websocket_connect("/v1/stream/stt?model=whisper-test", headers=AUTH) as ws:
            assert ws.receive_json() == {"type": "ready", "kind": "stt"}
            ws.send_text('{"type":"config"}')
            assert ws.receive_text() == 'echo:{"type":"config"}'
            ws.send_bytes(b"\x01\x02\x03")
            assert ws.receive_bytes() == b"\x03\x02\x01"


def test_upstream_close_code_reaches_caller(upstream_url):
    with TestClient(gateway([upstream_url])) as c:
        with c.websocket_connect("/v1/stream/tts?model=kokoro-test", headers=AUTH) as ws:
            ws.receive_json()
            ws.send_text("close")
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4000


def test_rejects_unauthenticated_and_wrong_kind(upstream_url):
    with TestClient(gateway([upstream_url])) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/v1/stream/stt?model=whisper-test") as ws:
                ws.receive_text()
        assert exc.value.code == 4401
        with pytest.raises(WebSocketDisconnect) as exc:  # a TTS alias on the STT endpoint
            with c.websocket_connect("/v1/stream/stt?model=kokoro-test", headers=AUTH) as ws:
                ws.receive_text()
        assert exc.value.code == 4404


def test_fails_over_from_dead_replica_before_accepting(upstream_url):
    dead = f"http://127.0.0.1:{free_port()}"
    app = gateway([dead, upstream_url])
    with TestClient(app) as c:
        for _ in range(2):  # round-robin start position must not matter
            with c.websocket_connect("/v1/stream/stt?model=whisper-test", headers=AUTH) as ws:
                assert ws.receive_json()["type"] == "ready"
        assert app.state.routes["whisper-test"].upstreams[0].healthy is False


def test_slot_held_for_stream_life_then_released(upstream_url):
    app = gateway([upstream_url], max_concurrency=1)
    us = app.state.routes["whisper-test"].upstreams[0]
    with TestClient(app) as c:
        with c.websocket_connect("/v1/stream/stt?model=whisper-test", headers=AUTH) as ws:
            ws.receive_json()
            assert us.sem.locked()
            with pytest.raises(WebSocketDisconnect) as exc:  # second stream: replica full
                with c.websocket_connect("/v1/stream/stt?model=whisper-test", headers=AUTH) as ws2:
                    ws2.receive_text()
            assert exc.value.code == 1013
        deadline = time.time() + 3
        while us.sem.locked() and time.time() < deadline:
            time.sleep(0.02)
        assert not us.sem.locked()


def test_one_shot_tts_routes_alias(upstream_url):
    with TestClient(gateway([upstream_url])) as c:
        r = c.post("/v1/audio/speech", headers=AUTH, json={"model": "kokoro-test", "input": "hello"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert r.content == b"RIFF:kokoro:hello"
        assert c.post("/v1/audio/speech", headers=AUTH, json={"model": "whisper-test", "input": "x"}).status_code == 404
