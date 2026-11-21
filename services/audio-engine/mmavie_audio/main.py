"""MMA Real-Time Audio Engine — Phase 1 shell.

Phase 4 adds the media path: SIP/RTP (G.711 u-law) and Asterisk AudioSocket at the edge,
jitter buffer, VAD/end-of-turn, barge-in cancel and the recording tap, ported from V1's
proven bridge code. Phase 1 fixes the service's configuration surface and health contract
so infrastructure (ports, security groups) can be built against it now.
"""
from __future__ import annotations

from fastapi import Depends
from pydantic import Field, model_validator

from mmavie_common.app import CheckResult, create_service_app
from mmavie_common.auth import make_auth_dependency
from mmavie_common.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "audio-engine"
    sip_port: int = Field(default=5062, ge=1024, le=65535)
    audiosocket_port: int = Field(default=9092, ge=1024, le=65535)
    rtp_port_min: int = Field(default=20000, ge=1024, le=65535)
    rtp_port_max: int = Field(default=20999, ge=1024, le=65535)
    pbx_allowed_cidrs: str = "127.0.0.1/32"
    max_calls: int = Field(default=25, ge=1, le=500)

    @model_validator(mode="after")
    def _ports(self) -> Settings:
        if self.rtp_port_max - self.rtp_port_min + 1 < 2 * self.max_calls:
            raise ValueError("RTP port range must hold at least 2 ports per concurrent call")
        return self


def build_app(settings: Settings | None = None):
    settings = settings or Settings()

    async def media_ready(_app) -> CheckResult:
        return CheckResult(True, "media path not enabled until Phase 4")

    app = create_service_app(settings, title="MMA Real-Time Audio Engine", readiness={"media": media_ready})
    auth = make_auth_dependency(settings)

    @app.get("/v1/info")
    async def info(_: str = Depends(auth)):
        return {"sip_udp": settings.sip_port, "audiosocket_tcp": settings.audiosocket_port,
                "rtp_udp": [settings.rtp_port_min, settings.rtp_port_max], "max_calls": settings.max_calls,
                "pbx_allowed_cidrs": [c.strip() for c in settings.pbx_allowed_cidrs.split(",") if c.strip()],
                "media_enabled": False}

    return app


def app_factory():
    return build_app()
