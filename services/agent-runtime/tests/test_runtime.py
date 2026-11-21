import httpx
from fastapi.testclient import TestClient

from mmavie_runtime.main import Settings, build_app

AUTH = {"Authorization": "Bearer dev-console"}
AGENT = {
    "agent_id": "fe_warm", "version": 1, "name": "Final Expense", "persona_prompt": "You are a polite assistant.",
    "models": {"stt": {"alias": "whisper-large-v3-turbo"}, "llm": {"alias": "qwen3-voice"},
               "tts": {"alias": "kokoro-82m"}, "voice": {"voice": "af_heart"}},
}


def app(gateway_status: int = 200):
    t = httpx.MockTransport(lambda req: httpx.Response(gateway_status, json={}))
    return build_app(Settings(environment="test", service_tokens="console:dev-console", gateway_url="http://gateway:8000"),
                     transport=t)


def test_ready_follows_gateway():
    with TestClient(app(200)) as c:
        assert c.get("/readyz").status_code == 200
    with TestClient(app(503)) as c:
        assert c.get("/readyz").status_code == 503


def test_validate_agent_version():
    with TestClient(app()) as c:
        assert c.post("/v1/validate/agent-version", headers=AUTH, json=AGENT).json()["valid"] is True
        bad = {**AGENT, "models": {**AGENT["models"], "voice": {"voice": "clone:../x"}}}
        assert c.post("/v1/validate/agent-version", headers=AUTH, json=bad).status_code == 422


def test_validate_policy_reports_hard_rules():
    with TestClient(app()) as c:
        r = c.post("/v1/validate/campaign-policy", headers=AUTH, json={
            "campaign_id": "fetest", "version": 1,
            "output_rules": [{"id": "no-gov", "pattern": r"\b(medicare|government) (approved|official)\b",
                              "reason": "no affiliation claims"}],
            "transfer": {"required_caller_facts": ["age_band", "interest"]}})
        assert r.json()["hard_rules"] == {"stop_phrases": 5, "output_rules": 1, "disclosures": 0, "transfer_facts": 2}
