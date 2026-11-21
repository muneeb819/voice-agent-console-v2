import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mmavie_audio.main import Settings, build_app


def test_rtp_range_must_fit_max_calls():
    with pytest.raises(ValidationError):
        Settings(environment="test", service_tokens="c:dev-x", max_calls=50, rtp_port_min=20000, rtp_port_max=20050)


def test_info_and_health():
    s = Settings(environment="test", service_tokens="console:dev-console", pbx_allowed_cidrs="10.0.5.10/32, 10.0.5.11/32")
    with TestClient(build_app(s)) as c:
        assert c.get("/readyz").status_code == 200
        info = c.get("/v1/info", headers={"Authorization": "Bearer dev-console"}).json()
        assert info["pbx_allowed_cidrs"] == ["10.0.5.10/32", "10.0.5.11/32"] and info["media_enabled"] is False
