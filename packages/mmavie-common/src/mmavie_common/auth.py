"""Service-to-service bearer-token authentication (constant-time comparison)."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from .settings import ServiceSettings


def _extract(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return request.headers.get("x-api-key", "").strip()


def make_auth_dependency(settings: ServiceSettings):
    """Return a FastAPI dependency that resolves to the authenticated caller name."""
    tokens = settings.token_map() if not settings.auth_disabled else {}

    async def require_caller(request: Request) -> str:
        if settings.auth_disabled:
            return "anonymous"
        presented = _extract(request)
        if presented:
            for secret, name in tokens.items():
                if hmac.compare_digest(presented.encode(), secret.encode()):
                    request.state.caller = name
                    return name
        raise HTTPException(status_code=401, detail="invalid or missing service token")

    return require_caller
