import pytest
from fastapi.testclient import TestClient

from mmavie_common.settings import ConfigError
from mmavie_llm_fake.main import Settings, build_app

AUTH = {"Authorization": "Bearer dev-gw"}


def client():
    return TestClient(build_app(Settings(environment="test", service_tokens="gateway:dev-gw")))


def test_refuses_production():
    with pytest.raises(ConfigError):
        build_app(Settings(environment="prod", service_tokens="gateway:" + "k" * 40))


def test_reply_and_unknown_model():
    with client() as c:
        r = c.post("/v1/chat/completions", headers=AUTH,
                   json={"model": "fake-llm", "messages": [{"role": "user", "content": "ping"}]})
        assert r.json()["choices"][0]["message"]["content"] == "Fake reply to: ping"
        assert c.post("/v1/chat/completions", headers=AUTH,
                      json={"model": "other", "messages": [{"role": "user", "content": "x"}]}).status_code == 404
