"""End-to-end smoke test of a running MMA-VIE stack (compose or `make local`).

Checks every service is live and ready, that the Gateway routes an LLM turn (plain and
streaming, plus a tool call) to its upstream, and that the runtime validates contracts.
Exit code 0 = pass. Prints one line per check with its latency so results can be recorded.

Defaults match compose.yaml (dev tokens only). Override with env vars for other targets.
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

E = os.environ.get
GATEWAY = E("SMOKE_GATEWAY_URL", "http://127.0.0.1:8080")
RUNTIME = E("SMOKE_RUNTIME_URL", "http://127.0.0.1:8081")
AUDIO = E("SMOKE_AUDIO_URL", "http://127.0.0.1:8082")
REGISTRY = E("SMOKE_REGISTRY_URL", "http://127.0.0.1:8083")
SPEECH = E("SMOKE_SPEECH_URL", "http://127.0.0.1:8001")
GW_TOKEN = E("SMOKE_GATEWAY_TOKEN", "dev-console-to-gateway")
RT_TOKEN = E("SMOKE_RUNTIME_TOKEN", "dev-console-to-runtime")
RG_TOKEN = E("SMOKE_REGISTRY_TOKEN", "dev-console-to-registry")
ALIAS = E("SMOKE_LLM_ALIAS", "qwen3-voice")

failures: list[str] = []


def check(name: str, fn) -> None:
    start = time.perf_counter()
    try:
        detail = fn()
        print(f"PASS  {name:<42} {1000 * (time.perf_counter() - start):7.1f} ms  {detail or ''}")
    except Exception as exc:  # noqa: BLE001 - report every failure, keep going
        failures.append(name)
        print(f"FAIL  {name:<42} {1000 * (time.perf_counter() - start):7.1f} ms  {type(exc).__name__}: {exc}")


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    c = httpx.Client(timeout=10)
    for name, url in [("gateway", GATEWAY), ("agent-runtime", RUNTIME), ("audio-engine", AUDIO),
                      ("model-registry", REGISTRY), ("speech", SPEECH)]:
        check(f"{name} /healthz", lambda url=url: expect(c.get(f"{url}/healthz").status_code == 200, "not live"))
        check(f"{name} /readyz", lambda url=url: (lambda r: (expect(r.status_code == 200, r.text[:200]), "")[1])(
            c.get(f"{url}/readyz")))

    gw = {"Authorization": f"Bearer {GW_TOKEN}"}
    check("gateway rejects missing token", lambda: expect(c.get(f"{GATEWAY}/v1/models").status_code == 401, "auth not enforced"))

    def models():
        data = c.get(f"{GATEWAY}/v1/models", headers=gw).json()["data"]
        unhealthy = [m["id"] for m in data if m["healthy_upstreams"] < 1]
        expect(not unhealthy, f"no healthy upstream for {unhealthy}")
        return ", ".join(m["id"] for m in data)
    check("gateway routes healthy", models)

    msg = [{"role": "user", "content": "smoke test"}]

    def plain():
        r = c.post(f"{GATEWAY}/v1/chat/completions", headers=gw, json={"model": ALIAS, "messages": msg})
        expect(r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        return repr(r.json()["choices"][0]["message"]["content"])[:60]
    check("LLM turn via gateway", plain)

    def streamed():
        first = None
        start = time.perf_counter()
        text = ""
        with c.stream("POST", f"{GATEWAY}/v1/chat/completions", headers=gw,
                      json={"model": ALIAS, "messages": msg, "stream": True}) as r:
            expect(r.status_code == 200, f"status {r.status_code}")
            for line in r.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                    if delta:
                        first = first or (time.perf_counter() - start)
                        text += delta
        expect(bool(text), "no streamed content")
        return f"first token {1000 * first:.1f} ms"
    check("LLM streamed turn via gateway", streamed)

    def tool():
        r = c.post(f"{GATEWAY}/v1/chat/completions", headers=gw, json={
            "model": ALIAS, "messages": [{"role": "user", "content": "transfer me please"}],
            "tools": [{"type": "function", "function": {"name": "transfer_to_live_agent", "parameters": {}}}]})
        calls = r.json()["choices"][0]["message"].get("tool_calls") or []
        expect(calls and calls[0]["function"]["name"] == "transfer_to_live_agent", "tool call missing")
    check("LLM tool call via gateway", tool)

    def validate():
        agent = {"agent_id": "smoke", "version": 1, "name": "Smoke", "persona_prompt": "Be brief.",
                 "models": {"stt": {"alias": "whisper-large-v3-turbo"}, "llm": {"alias": ALIAS},
                            "tts": {"alias": "kokoro-82m"}, "voice": {"voice": "af_heart"}}}
        r = c.post(f"{RUNTIME}/v1/validate/agent-version", headers={"Authorization": f"Bearer {RT_TOKEN}"}, json=agent)
        expect(r.status_code == 200 and r.json()["valid"], r.text[:200])
    check("runtime validates agent contract", validate)

    def registry():
        r = c.get(f"{REGISTRY}/v1/models", headers={"Authorization": f"Bearer {RG_TOKEN}"})
        expect(r.status_code == 200, r.text[:200])
        return f"{len(r.json()['data'])} models registered"
    check("model registry reads database", registry)

    print(f"\n{'PASSED' if not failures else 'FAILED'}: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
