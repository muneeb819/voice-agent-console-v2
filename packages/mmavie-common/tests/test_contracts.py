import pytest
from pydantic import TypeAdapter, ValidationError

from mmavie_common.contracts.agent import AgentModels, AgentVersion, FunctionSpec, ModelPin, VoicePin
from mmavie_common.contracts.events import SessionEvent
from mmavie_common.contracts.policy import CallingWindow, CampaignPolicy, OutputRule
from mmavie_common.contracts.routing import RouteTable

MODELS = AgentModels(
    stt=ModelPin(alias="whisper-large-v3-turbo"),
    llm=ModelPin(alias="qwen3-voice", params={"temperature": 0.4}),
    tts=ModelPin(alias="kokoro-82m"),
    voice=VoicePin(voice="af_heart"),
)


def test_agent_version_valid_and_per_agent_models():
    v = AgentVersion(agent_id="fe_warm", version=1, name="Final Expense", persona_prompt="You are…", models=MODELS,
                     functions=[FunctionSpec(name="transfer_to_live_agent", description="x", builtin=True)])
    assert v.models.llm.alias == "qwen3-voice"


def test_agent_rejects_duplicate_and_fake_builtin_functions():
    with pytest.raises(ValidationError):
        AgentVersion(agent_id="a", version=1, name="a", persona_prompt="p", models=MODELS,
                     functions=[FunctionSpec(name="x", description=""), FunctionSpec(name="x", description="")])
    with pytest.raises(ValidationError):
        AgentVersion(agent_id="a", version=1, name="a", persona_prompt="p", models=MODELS,
                     functions=[FunctionSpec(name="wire_money", description="", builtin=True)])


def test_voice_pin_accepts_stock_and_clone_ids_only():
    VoicePin(voice="clone:" + "a" * 32)
    with pytest.raises(ValidationError):
        VoicePin(voice="clone:../../etc")


def test_policy_validation():
    p = CampaignPolicy(campaign_id="fetest", version=1, stop_phrases=["  Stop Calling  "])
    assert p.stop_phrases == ["stop calling"]
    with pytest.raises(ValidationError):
        OutputRule(id="bad", pattern="(unclosed", reason="x")
    with pytest.raises(ValidationError):
        CallingWindow(start="21:00", end="08:00")


def test_session_event_discriminated_union():
    ev = TypeAdapter(SessionEvent).validate_python(
        {"type": "barge_in", "session_id": "sess-12345", "speech_to_silence_ms": 180}
    )
    assert ev.type == "barge_in" and ev.speech_to_silence_ms == 180


@pytest.mark.parametrize("url", ["http://llm:8000", "http://10.0.3.7:8000", "http://llm-1.mmavie.internal:8000",
                                 "http://127.0.0.1:9000"])
def test_routes_accept_internal_upstreams(url):
    RouteTable(routes=[{"alias": "a", "kind": "llm", "upstream_model": "m", "upstreams": [{"url": url}]}])


@pytest.mark.parametrize("url", ["https://api.openai.com", "http://8.8.8.8", "https://agent.deepgram.com", "ftp://llm"])
def test_routes_reject_external_upstreams(url):
    with pytest.raises(ValidationError):
        RouteTable(routes=[{"alias": "a", "kind": "llm", "upstream_model": "m", "upstreams": [{"url": url}]}])
