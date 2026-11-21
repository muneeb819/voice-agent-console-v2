"""MMA AI Gateway routing table: model alias -> upstream replicas.

Loaded from a JSON file (MMAVIE_GATEWAY_ROUTES_FILE). Upstreams are always internal
addresses; the Gateway never routes to an external AI provider.
"""
from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

RouteKind = Literal["llm", "stt", "tts"]


class Upstream(BaseModel):
    url: str
    # Name of the env var holding the bearer token for this upstream (never the secret itself).
    token_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    max_concurrency: int = Field(default=64, ge=1, le=4096)

    @field_validator("url")
    @classmethod
    def _internal_only(cls, url: str) -> str:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("upstream url must be http(s)://host[:port]")
        host = p.hostname
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            if not (ip.is_private or ip.is_loopback):
                raise ValueError(f"upstream {host} is a public address; only internal upstreams are allowed")
        elif "." in host and not host.endswith((".internal", ".local", ".svc", ".mmavie")):
            # Hostnames: single-label service names (compose/ECS) or internal DNS zones only.
            raise ValueError(
                f"upstream host '{host}' is not an internal name (use a service name, *.internal or a private IP)"
            )
        return url.rstrip("/")


class Route(BaseModel):
    alias: str = Field(pattern=r"^[A-Za-z0-9._:/-]+$")
    kind: RouteKind
    upstream_model: str = Field(min_length=1, description="Model name the upstream server expects")
    upstreams: list[Upstream] = Field(min_length=1, max_length=32)


class RouteTable(BaseModel):
    routes: list[Route]

    @field_validator("routes")
    @classmethod
    def _unique(cls, routes: list[Route]) -> list[Route]:
        aliases = [r.alias for r in routes]
        if len(aliases) != len(set(aliases)):
            raise ValueError("route aliases must be unique")
        return routes

    def get(self, alias: str) -> Route | None:
        return next((r for r in self.routes if r.alias == alias), None)
