"""MMA Agent Runtime — Phase 1 foundation.

The per-call conversation state machine and the policy engine land in Phase 4. Phase 1
provides the service shell, its dependency on the Gateway, and the validation endpoint
Console V2 will call before saving an agent version or campaign policy, so invalid
configuration is rejected by the same contracts the runtime will execute.
"""
from __future__ import annotations

import httpx
from fastapi import Depends

from mmavie_common.app import CheckResult, create_service_app
from mmavie_common.auth import make_auth_dependency
from mmavie_common.contracts import CONTRACT_VERSION
from mmavie_common.contracts.agent import AgentVersion
from mmavie_common.contracts.policy import CampaignPolicy
from mmavie_common.settings import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "agent-runtime"
    gateway_url: str = "http://gateway:8000"


def build_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
    settings = settings or Settings()

    async def gateway_ready(_app) -> CheckResult:
        async with httpx.AsyncClient(transport=transport, timeout=2) as c:
            r = await c.get(f"{settings.gateway_url}/readyz")
        return CheckResult(r.status_code == 200, f"gateway readyz {r.status_code}")

    app = create_service_app(settings, title="MMA Agent Runtime", readiness={"gateway": gateway_ready})
    auth = make_auth_dependency(settings)

    @app.post("/v1/validate/agent-version")
    async def validate_agent(spec: AgentVersion, _: str = Depends(auth)):
        return {"valid": True, "contract_version": CONTRACT_VERSION, "agent_id": spec.agent_id, "version": spec.version}

    @app.post("/v1/validate/campaign-policy")
    async def validate_policy(policy: CampaignPolicy, _: str = Depends(auth)):
        return {"valid": True, "contract_version": CONTRACT_VERSION, "campaign_id": policy.campaign_id,
                "hard_rules": {"stop_phrases": len(policy.stop_phrases), "output_rules": len(policy.output_rules),
                               "disclosures": len(policy.disclosures),
                               "transfer_facts": len(policy.transfer.required_caller_facts)}}

    return app


def app_factory():
    return build_app()
