import json

import httpx
import pytest
from fastapi.testclient import TestClient

from mmavie_common.contracts.routing import RouteTable
from mmavie_gateway.main import Settings, build_app
from mmavie_llm_fake.main import Settings as FakeSettings
from mmavie_llm_fake.main import build_app as build_fake

AUTH = {"Authorization": "Bearer dev-console"}


class HostRouter(httpx.AsyncBaseTransport):
    """Dispatch by host to in-process ASGI apps; unknown hosts behave like a dead server."""

    def __init__(self, apps: dict[str, object]):
        self.transports = {h: httpx.ASGITransport(app=a) for h, a in apps.items()}

    async def handle_async_request(self, request):
        t = self.transports.get(request.url.host)
        if t is None:
            raise httpx.ConnectError("connection refused", request=request)
        return await t.handle_async_request(request)


def fake_llm():
    return build_fake(FakeSettings(environment="test", service_tokens="gateway:dev-gw"))


def gateway(upstreams: list[str], max_concurrency: int = 4):
    import os
    os.environ["LLM_TOKEN"] = "dev-gw"
    routes = RouteTable(routes=[{
        "alias": "qwen3-voice", "kind": "llm", "upstream_model": "fake-llm",
        "upstreams": [{"url": u, "token_env": "LLM_TOKEN", "max_concurrency": max_concurrency} for u in upstreams],
    }])
    settings = Settings(environment="test", service_tokens="console:dev-console", gateway_probe_interval_s=3600)
    return build_app(settings, routes=routes, transport=HostRouter({"llm-a": fake_llm()}))


def test_requires_auth_and_known_alias():
    with TestClient(gateway(["http://llm-a:8000"])) as c:
        assert c.post("/v1/chat/completions", json={"model": "qwen3-voice", "messages": []}).status_code == 401
        r = c.post("/v1/chat/completions", headers=AUTH, json={"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 404


def test_ready_and_models():
    with TestClient(gateway(["http://llm-a:8000"])) as c:
        assert c.get("/readyz").json()["ready"] is True
        m = c.get("/v1/models", headers=AUTH).json()["data"][0]
        assert m["id"] == "qwen3-voice" and m["healthy_upstreams"] == 1


def test_non_stream_completion_routes_alias_to_upstream_model():
    with TestClient(gateway(["http://llm-a:8000"])) as c:
        r = c.post("/v1/chat/completions", headers=AUTH,
                   json={"model": "qwen3-voice", "messages": [{"role": "user", "content": "hello there"}]})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Fake reply to: hello there"


def test_stream_completion_relays_sse():
    with TestClient(gateway(["http://llm-a:8000"])) as c:
        with c.stream("POST", "/v1/chat/completions", headers=AUTH,
                      json={"model": "qwen3-voice", "stream": True, "messages": [{"role": "user", "content": "one two"}]}) as r:
            lines = [ln for ln in r.iter_lines() if ln.startswith("data: ")]
        assert lines[-1] == "data: [DONE]"
        text = "".join(json.loads(ln[6:])["choices"][0]["delta"].get("content", "") for ln in lines[:-1])
        assert text == "Fake reply to: one two"


def test_tool_call_passes_through():
    with TestClient(gateway(["http://llm-a:8000"])) as c:
        r = c.post("/v1/chat/completions", headers=AUTH, json={
            "model": "qwen3-voice", "messages": [{"role": "user", "content": "please transfer me"}],
            "tools": [{"type": "function", "function": {"name": "transfer_to_live_agent", "parameters": {}}}]})
        msg = r.json()["choices"][0]["message"]
        assert msg["tool_calls"][0]["function"]["name"] == "transfer_to_live_agent"


def test_failover_from_dead_upstream():
    with TestClient(gateway(["http://llm-dead:8000", "http://llm-a:8000"])) as c:
        health = c.get("/v1/routes/health", headers=AUTH).json()["qwen3-voice"]
        assert [u["healthy"] for u in health] == [False, True]
        assert c.get("/readyz").json()["ready"] is True
        for _ in range(3):  # round-robin must never surface the dead replica's failure
            r = c.post("/v1/chat/completions", headers=AUTH,
                       json={"model": "qwen3-voice", "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200


def test_not_ready_when_no_healthy_upstream():
    with TestClient(gateway(["http://llm-dead:8000"])) as c:
        assert c.get("/readyz").status_code == 503
        r = c.post("/v1/chat/completions", headers=AUTH,
                   json={"model": "qwen3-voice", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 502


@pytest.mark.anyio
async def test_load_shedding_when_upstream_full():
    app = gateway(["http://llm-a:8000"], max_concurrency=1)
    with TestClient(app) as c:
        us = app.state.routes["qwen3-voice"].upstreams[0]
        await us.sem.acquire()
        try:
            r = c.post("/v1/chat/completions", headers=AUTH,
                       json={"model": "qwen3-voice", "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 503 and r.headers["retry-after"] == "1"
        finally:
            us.sem.release()
