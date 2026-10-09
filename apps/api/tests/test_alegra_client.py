import base64

import httpx
import pytest

from app.services import alegra_client as alegra_client_module
from app.services.alegra_client import (AlegraAuthError, AlegraClient, AlegraNetworkError,
                                        AlegraNotConfigured, AlegraRateLimitError,
                                        AlegraServerError, AlegraValidationError)

SECRET_TOKEN = "super-secret-token-should-never-leak"


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(alegra_client_module.settings, "alegra_enabled", True)
    yield
    monkeypatch.setattr(alegra_client_module.settings, "alegra_enabled", False)


def client_with(handler, **kwargs) -> AlegraClient:
    return AlegraClient(base_url="https://api.alegra.test/api/v1", email="shop@example.com",
                        token=SECRET_TOKEN, transport=httpx.MockTransport(handler), **kwargs)


def test_disabled_raises_without_any_request(enabled, monkeypatch):
    monkeypatch.setattr(alegra_client_module.settings, "alegra_enabled", False)
    called = False
    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})
    client = client_with(handler)
    with pytest.raises(AlegraNotConfigured):
        client.get("/company")
    assert called is False


def test_missing_credentials_raises_before_request(enabled):
    called = False
    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json={})
    client = AlegraClient(base_url="https://api.alegra.test/api/v1", email="", token="",
                          transport=httpx.MockTransport(handler))
    with pytest.raises(AlegraNotConfigured):
        client.get("/company")
    assert called is False


def test_auth_header_is_basic_base64_email_colon_token(enabled):
    seen = {}
    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})
    client = client_with(handler)
    client.get("/company")
    assert seen["authorization"] == "Basic " + base64.b64encode(
        f"shop@example.com:{SECRET_TOKEN}".encode()).decode()


def test_successful_response_is_parsed_json(enabled):
    def handler(request):
        return httpx.Response(200, json={"name": "Tienda"})
    client = client_with(handler)
    assert client.get("/company") == {"name": "Tienda"}


def test_401_maps_to_auth_error(enabled):
    client = client_with(lambda request: httpx.Response(401, json={"message": "unauthorized"}))
    with pytest.raises(AlegraAuthError):
        client.get("/company")


def test_403_maps_to_auth_error(enabled):
    client = client_with(lambda request: httpx.Response(403, json={"message": "forbidden"}))
    with pytest.raises(AlegraAuthError):
        client.get("/company")


def test_400_maps_to_validation_error_with_details(enabled):
    client = client_with(lambda request: httpx.Response(400, json={"message": "numberTemplate inválido"}))
    with pytest.raises(AlegraValidationError) as exc_info:
        client.post("/invoices", json={})
    assert "numberTemplate" in str(exc_info.value)
    assert exc_info.value.details["message"] == "numberTemplate inválido"


def test_429_maps_to_rate_limit_error(enabled):
    client = client_with(lambda request: httpx.Response(429, json={"message": "too many requests"}))
    with pytest.raises(AlegraRateLimitError):
        client.get("/company")


def test_5xx_maps_to_server_error(enabled):
    client = client_with(lambda request: httpx.Response(503, text="down"))
    with pytest.raises(AlegraServerError):
        client.get("/company")


def test_invalid_json_maps_to_generic_alegra_error(enabled):
    client = client_with(lambda request: httpx.Response(200, text="<html>not json</html>"))
    with pytest.raises(Exception):
        client.get("/company")


def test_timeout_maps_to_network_error(enabled):
    def handler(request):
        raise httpx.TimeoutException("timed out", request=request)
    client = client_with(handler)
    with pytest.raises(AlegraNetworkError):
        client.get("/company")


def test_connection_error_maps_to_network_error(enabled):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    client = client_with(handler)
    with pytest.raises(AlegraNetworkError):
        client.get("/company")


def test_no_secret_leaks_into_logs_on_success(enabled, caplog):
    caplog.set_level("DEBUG")
    client = client_with(lambda request: httpx.Response(200, json={"ok": True}))
    client.get("/company")
    assert SECRET_TOKEN not in caplog.text
    assert "Authorization" not in caplog.text
    assert "Basic " not in caplog.text


def test_no_secret_leaks_into_logs_or_exception_on_error(enabled, caplog):
    caplog.set_level("DEBUG")
    client = client_with(lambda request: httpx.Response(401, json={"message": "unauthorized"}))
    with pytest.raises(AlegraAuthError) as exc_info:
        client.get("/company")
    assert SECRET_TOKEN not in caplog.text
    assert SECRET_TOKEN not in str(exc_info.value)
