"""Centralized, low-level Alegra HTTP client.

No business logic lives here — only auth, transport, timeouts, and mapping
every failure mode to a typed internal exception. AlegraProvider builds on
top of this; electronic_invoicing.py never touches httpx directly.

Verified against the official docs (developer.alegra.com) on 2026-09-23:
Basic Auth base64(email:token), base URL https://api.alegra.com/api/v1,
rate limit 150 req/min -> 429.
"""
import base64
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class AlegraError(Exception):
    """Base for every Alegra-related failure. Messages here are safe to log
    and to surface to the frontend — never include the token or the
    Authorization header."""


class AlegraNotConfigured(AlegraError):
    """ALEGRA_ENABLED=false, or ALEGRA_EMAIL/ALEGRA_TOKEN missing."""


class AlegraAuthError(AlegraError):
    """401/403 — invalid credentials or insufficient permission."""


class AlegraValidationError(AlegraError):
    """400 — Alegra rejected the request body. `details` is the sanitized
    (secret-free) JSON body Alegra returned, kept for debugging."""
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


class AlegraRateLimitError(AlegraError):
    """429 — over the 150 req/min limit."""


class AlegraServerError(AlegraError):
    """5xx from Alegra."""


class AlegraNetworkError(AlegraError):
    """Timeout or transport-level failure reaching Alegra."""


def error_message(body: Any) -> str | None:
    """Human message of an Alegra error body, wherever Alegra put it: top-level
    `message`, nested `error.message` / `error` string, or an `errors` list.
    Codes are appended when present. None when the body carries no text."""
    if not isinstance(body, dict):
        return str(body)[:300] if body else None
    parts: list[str] = []

    def take(node: Any) -> None:
        if isinstance(node, str) and node.strip():
            parts.append(node.strip())
        elif isinstance(node, dict):
            text = node.get("message") or node.get("description") or node.get("detail")
            code = node.get("code")
            if text:
                parts.append(f"{text} (código {code})" if code not in (None, "") else str(text))
            for key in ("error", "errors"):
                if key in node and node[key] is not node:
                    take(node[key])
        elif isinstance(node, list):
            for entry in node[:10]:
                take(entry)

    take(body)
    seen = list(dict.fromkeys(parts))
    return "; ".join(seen)[:500] if seen else None


class AlegraClient:
    def __init__(self, base_url: str | None = None, email: str | None = None,
                token: str | None = None, timeout: float = 15.0,
                transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url if base_url is not None else settings.alegra_api_url).rstrip("/")
        self.email = email if email is not None else settings.alegra_email
        self.token = token if token is not None else settings.alegra_token
        self.timeout = timeout
        # Injectable only for tests (httpx.MockTransport) — production never sets this.
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.email and self.token)

    def _auth_header(self) -> dict[str, str]:
        if not self.configured:
            raise AlegraNotConfigured("ALEGRA_EMAIL/ALEGRA_TOKEN no están configurados")
        raw = f"{self.email}:{self.token}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: dict | None = None) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: dict | None = None) -> Any:
        return self.request("PUT", path, json=json)

    def request(self, method: str, path: str, *, params: dict | None = None,
               json: dict | None = None) -> Any:
        if not settings.alegra_enabled:
            raise AlegraNotConfigured("ALEGRA_ENABLED=false")
        headers = {**self._auth_header(), "Accept": "application/json"}
        start = time.monotonic()
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
                response = client.request(method, path, params=params, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("alegra_request_timeout method=%s path=%s duration=%.2fs",
                           method, path, time.monotonic() - start)
            raise AlegraNetworkError(f"Tiempo de espera agotado consultando Alegra ({path})") from exc
        except httpx.RequestError as exc:
            logger.warning("alegra_request_network_error method=%s path=%s error=%s",
                           method, path, type(exc).__name__)
            raise AlegraNetworkError(f"Error de red consultando Alegra ({path})") from exc
        duration = time.monotonic() - start
        logger.info("alegra_request method=%s path=%s status=%s duration=%.2fs",
                    method, path, response.status_code, duration)
        return self._handle_response(response)

    def _handle_response(self, response: httpx.Response) -> Any:
        if response.status_code in (401, 403):
            raise AlegraAuthError("Credenciales de Alegra inválidas o sin permiso para este recurso")
        if response.status_code == 429:
            raise AlegraRateLimitError("Límite de solicitudes de Alegra alcanzado (150/min); intenta más tarde")
        if response.status_code == 400:
            body = self._safe_json(response)
            raise AlegraValidationError(error_message(body) or "Alegra rechazó la solicitud (400)", details=body)
        if response.status_code >= 500:
            raise AlegraServerError(f"Alegra respondió con error de servidor ({response.status_code})")
        if not response.is_success:
            raise AlegraError(f"Alegra respondió con estado inesperado ({response.status_code})")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AlegraError("Alegra devolvió una respuesta que no es JSON válido") from exc

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"message": response.text[:300]}
