"""Shared test guard: the host .env reaches the api container (docker compose passes it), so
real operational flags must never leak into tests. Each test starts with them OFF; a test
that needs one turns it on explicitly with monkeypatch."""
import pytest


@pytest.fixture(autouse=True)
def _flags_off_by_default(monkeypatch):
    from app.core.config import settings
    for flag in ("alegra_catalog_create_enabled", "alegra_auto_issue", "alegra_auto_issue_stamp",
                 "alegra_manual_issue_enabled"):
        monkeypatch.setattr(settings, flag, False)
