"""Opt-in FastAPI -> agent -> analytics -> isolated PostgreSQL smoke test."""

import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import Base
from app.domain.enums import SaleStatus
from app.main import app
from app.models import Product, Sale, SaleItem, User
from app.services.business_agent import run_business_agent
from app.services.business_analytics import PosBusinessAnalytics
from test_business_agent import ScriptedModel, period_args


@pytest.mark.skipif(os.getenv("COMP_S2_004_PG_TEST") != "1", reason="requires disposable PostgreSQL")
def test_http_follow_up_uses_readonly_postgresql(monkeypatch):
    admin_url = os.environ["COMP_S2_004_ADMIN_URL"]
    read_url = os.environ["COMP_S2_004_READ_URL"]
    if "@comps2004-db:5432/comp_s2_004" not in admin_url or "@comps2004-db:5432/comp_s2_004" not in read_url:
        pytest.fail("Refusing non-experimental PostgreSQL destination")
    engine = create_engine(admin_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(name="Synthetic operator")
        product = Product(name="Synthetic rice", sale_price=200, unit_cost=100)
        db.add_all([user, product])
        db.flush()
        sale = Sale(number="COMP-S2-004-1", status=SaleStatus.COMPLETED,
                    created_by=user.id, total=Decimal("400"),
                    completed_at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc))
        db.add(sale)
        db.flush()
        db.add(SaleItem(sale_id=sale.id, product_id=product.id, product_name=product.name,
                        quantity=2, unit_price=200, unit_cost=100, line_total=400))
        db.commit()
    with engine.begin() as connection:
        connection.execute(text("CREATE ROLE comps2004_reader LOGIN PASSWORD 'synthetic-readonly-test'"))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO comps2004_reader"))
        connection.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO comps2004_reader"))
    read_engine = create_engine(read_url)
    with Session(read_engine) as read_db:
        assert PosBusinessAnalytics(read_db).sales_summary(date(2026, 11, 24),
                                                             date(2026, 11, 24)).net_sales == 400
    read_engine.dispose()
    monkeypatch.setattr(settings, "business_agent_enabled", True)
    monkeypatch.setattr(settings, "business_agent_access_token", "synthetic-test-bearer-token-32-chars")
    monkeypatch.setattr(settings, "business_agent_openai_api_key", "synthetic-not-sent")
    monkeypatch.setattr(settings, "business_agent_database_url", read_url)

    async def scripted_run(question, analytics, *, model_override=None, history=None):
        model = ScriptedModel([[('get_sales_summary', period_args())], "Venta sintética verificada: 400."])
        return await run_business_agent(question, analytics, model_override=model, history=history)

    monkeypatch.setattr("app.services.business_agent_conversations.run_business_agent", scripted_run)
    client = TestClient(app)
    headers = {"Authorization": "Bearer synthetic-test-bearer-token-32-chars",
               "X-Conversation-Session": "a" * 64}
    first = client.post("/api/v1/agent/chat", headers=headers,
                        json={"question": "Ventas del 24 de noviembre de 2026?"})
    second = client.post("/api/v1/agent/chat", headers=headers,
                         json={"question": "Y ese mismo periodo?"})
    assert first.status_code == second.status_code == 200
    assert first.json()["tools_used"] == second.json()["tools_used"] == ["get_sales_summary"]
    assert first.json()["periods"][0]["timezone"] == "America/Bogota"
    assert first.json()["usage"]["model_calls"] == 2
    engine.dispose()
