import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from mmavie_common.app import CheckResult, create_service_app
from mmavie_common.auth import make_auth_dependency
from mmavie_common.settings import ConfigError, ServiceSettings


def make(**kw) -> ServiceSettings:
    base = dict(service_name="t", environment="test", service_tokens="console:dev-secret")
    base.update(kw)
    return ServiceSettings(**base)


def test_fails_closed_without_tokens():
    with pytest.raises(ConfigError):
        make(service_tokens="").validate_for_start()


def test_auth_disabled_refused_in_prod():
    with pytest.raises(ConfigError):
        make(environment="prod", auth_disabled=True).validate_for_start()


def test_dev_or_short_tokens_refused_in_prod():
    with pytest.raises(ConfigError):
        make(environment="prod", service_tokens="console:dev-" + "x" * 40).validate_for_start()
    with pytest.raises(ConfigError):
        make(environment="staging", service_tokens="console:short").validate_for_start()
    make(environment="prod", service_tokens="console:" + "k" * 40).validate_for_start()


def test_token_map_parses_named_and_bare():
    s = make(service_tokens="console:aaa, bbb ,runtime:ccc")
    assert s.token_map() == {"aaa": "console", "bbb": "client", "ccc": "runtime"}


def _app(ready_ok: bool = True):
    s = make()

    async def check(_app):
        return CheckResult(ready_ok, "fine" if ready_ok else "down")

    app = create_service_app(s, title="t", readiness={"dep": check})
    auth = make_auth_dependency(s)

    @app.get("/secure")
    async def secure(caller: str = Depends(auth)):
        return {"caller": caller}

    return app


def test_health_ready_metrics_and_request_id():
    with TestClient(_app()) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.headers["x-request-id"]
        assert c.get("/readyz").json()["ready"] is True
        assert "mmavie_http_request_seconds" in c.get("/metrics").text


def test_not_ready_returns_503():
    with TestClient(_app(ready_ok=False)) as c:
        r = c.get("/readyz")
        assert r.status_code == 503 and r.json()["checks"]["dep"]["detail"] == "down"


def test_bearer_and_api_key_auth():
    with TestClient(_app()) as c:
        assert c.get("/secure").status_code == 401
        assert c.get("/secure", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/secure", headers={"Authorization": "Bearer dev-secret"}).json() == {"caller": "console"}
        assert c.get("/secure", headers={"X-API-Key": "dev-secret"}).status_code == 200
