"""Deterministic OpenAI-compatible LLM stand-in for dev and CI. NEVER deploy to staging/prod.

It lets every other service be built and tested on a laptop without a GPU. Replies are
predictable so tests can assert on them. The real LLM Engine (vLLM) exposes the same
/v1/chat/completions contract, so swapping it in is a routing change only.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mmavie_common.app import CheckResult, create_service_app
from mmavie_common.auth import make_auth_dependency
from mmavie_common.settings import ConfigError, ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "llm-fake"
    fake_model: str = "fake-llm"
    fake_token_delay_ms: int = 0


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float | None = None


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def plan_reply(req: ChatRequest) -> tuple[str, dict | None]:
    """Return (text, tool_call). A 'transfer' request triggers the transfer tool when offered."""
    text = _last_user_text(req.messages)
    tool_names = {t.get("function", {}).get("name") for t in (req.tools or [])}
    if "transfer" in text.lower() and "transfer_to_live_agent" in tool_names:
        return "", {"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                    "function": {"name": "transfer_to_live_agent", "arguments": json.dumps({"reason": "caller asked"})}}
    return f"Fake reply to: {text.strip()[:200]}", None


def build_app(settings: Settings | None = None):
    settings = settings or Settings()
    if settings.is_production_like:
        raise ConfigError("llm-fake must never run in staging/prod")

    async def always_ready(_app) -> CheckResult:
        return CheckResult(True, "fake backend")

    app = create_service_app(settings, title="MMA-VIE fake LLM (dev only)", readiness={"backend": always_ready})
    auth = make_auth_dependency(settings)

    @app.get("/v1/models")
    async def models(_: str = Depends(auth)):
        return {"object": "list", "data": [{"id": settings.fake_model, "object": "model", "owned_by": "mmavie-dev"}]}

    @app.post("/v1/chat/completions")
    async def chat(req: ChatRequest, _: str = Depends(auth)):
        if req.model != settings.fake_model:
            raise HTTPException(404, f"model '{req.model}' not served here")
        text, tool = plan_reply(req)
        cid, created = "chatcmpl-" + uuid.uuid4().hex[:16], int(time.time())
        if not req.stream:
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool:
                msg["tool_calls"] = [tool]
            return {"id": cid, "object": "chat.completion", "created": created, "model": req.model,
                    "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool else "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": len(text.split()), "total_tokens": len(text.split())}}

        async def gen():
            def chunk(delta: dict, finish: str | None = None) -> str:
                body = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": req.model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                return f"data: {json.dumps(body)}\n\n"

            yield chunk({"role": "assistant"})
            if tool:
                yield chunk({"tool_calls": [{"index": 0, **tool}]})
                yield chunk({}, "tool_calls")
            else:
                for i, word in enumerate(text.split(" ")):
                    if settings.fake_token_delay_ms:
                        await asyncio.sleep(settings.fake_token_delay_ms / 1000)
                    yield chunk({"content": word if i == 0 else " " + word})
                yield chunk({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def app_factory():
    return build_app()
