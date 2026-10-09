from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import CashMovementType, InventoryMovementType, PaymentMethod
from app.main import app
from app.models import (CashMovement, CashSession, InventoryMovement,
                        PaymentChannel, Product, User)
from app.services.pos import add_item, create_sale


@pytest.fixture()
def cash_context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        product = Product(barcode="cash-1", name="Producto", sale_price=Decimal("100000"),
                          unit_cost=Decimal("50000"))
        channels = [
            PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True, display_order=10),
            PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=20),
            PaymentChannel(name="Daviplata", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=30),
            PaymentChannel(name="Bre-B / Llave", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=40),
            PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD, affects_cash=False, display_order=50),
        ]
        db.add_all([user, product, *channels]); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
               quantity=Decimal("100"), unit_cost=product.unit_cost, user_id=user.id)); db.commit()

        def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), product, {channel.name: channel for channel in channels}
        finally:
            app.dependency_overrides.clear()


def open_sale(db, product):
    sale = create_sale(db)
    return add_item(db, sale.id, product.barcode, Decimal("1"))


def open_cash(client, amount="200000", note="Base turno mañana"):
    return client.post("/api/v1/cash/open", json={"opening_amount": amount, "note": note})


def checkout(client, sale, channel, received=None):
    body = {"channel_id": str(channel.id)}
    if received is not None:
        body["received_amount"] = received
    return client.post(f"/api/v1/sales/{sale.id}/checkout", json=body)


def test_open_cash_and_note_and_second_open_rejected(cash_context):
    db, client, _, _ = cash_context
    response = open_cash(client)
    assert response.status_code == 201
    assert response.json()["session"]["status"] == "OPEN"
    assert response.json()["session"]["opening_note"] == "Base turno mañana"
    assert client.post("/api/v1/cash/open", json={"opening_amount": "0"}).status_code == 409


@pytest.mark.parametrize("channel_name", ["Nequi", "Daviplata", "Bre-B / Llave", "Datáfono"])
def test_digital_sale_requires_open_journey(cash_context, channel_name):
    _, client, _, _ = cash_context
    response = client.post("/api/v1/sales")
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "OPERATIONAL_SESSION_CLOSED",
        "message": "La jornada está cerrada. Abre la caja para registrar esta operación."}


def test_cash_sale_requires_open_journey(cash_context):
    _, client, _, _ = cash_context
    response = client.post("/api/v1/sales")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OPERATIONAL_SESSION_CLOSED"


def test_cash_sale_and_change_create_net_sale_once(cash_context):
    db, client, product, channels = cash_context
    open_cash(client)
    sale = open_sale(db, product)
    response = checkout(client, sale, channels["Efectivo"], "150000")
    assert response.status_code == 200
    assert Decimal(response.json()["payments"][0]["change_amount"]) == Decimal("50000")
    movements = db.scalars(select(CashMovement).where(CashMovement.reference_id == sale.id)).all()
    assert len(movements) == 1
    assert movements[0].amount == Decimal("100000")


def test_mixed_sale_affects_only_cash_component_and_summary_channels(cash_context):
    db, client, product, channels = cash_context
    open_cash(client)
    sale = open_sale(db, product)
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Efectivo"].id), "amount": "40000", "received_amount": "40000"},
        {"channel_id": str(channels["Nequi"].id), "amount": "60000"}]})
    assert response.status_code == 200
    summary = client.get("/api/v1/cash/current").json()
    assert Decimal(summary["cash_sales"]) == Decimal("40000")
    assert Decimal(summary["expected_cash"]) == Decimal("240000")
    assert Decimal(summary["total_sales"]) == Decimal("100000")
    assert summary["transaction_count"] == 1
    rows = {row["channel_name"]: row for row in summary["payment_channels"]}
    assert Decimal(rows["Efectivo"]["amount"]) == Decimal("40000")
    assert Decimal(rows["Nequi"]["amount"]) == Decimal("60000")


def test_deposit_requires_reason_and_updates_expected_cash(cash_context):
    _, client, _, _ = cash_context
    open_cash(client)
    assert client.post("/api/v1/cash/deposit", json={"amount": "20000", "reason": ""}).status_code == 422
    response = client.post("/api/v1/cash/deposit", json={"amount": "20000", "reason": "Cambio adicional"})
    assert response.status_code == 201
    assert Decimal(response.json()["amount"]) == Decimal("20000")
    assert Decimal(client.get("/api/v1/cash/current").json()["expected_cash"]) == Decimal("220000")


def test_withdrawal_requires_reason_is_negative_and_cannot_exceed_expected(cash_context):
    _, client, _, _ = cash_context
    open_cash(client)
    assert client.post("/api/v1/cash/withdrawal", json={"amount": "1", "reason": "", "withdrawal_reason": "OTHER"}).status_code == 422
    response = client.post("/api/v1/cash/withdrawal", json={"amount": "50000", "reason": "Retiro preventivo", "withdrawal_reason": "OTHER"})
    assert response.status_code == 201
    assert Decimal(response.json()["amount"]) == Decimal("-50000")
    assert client.post("/api/v1/cash/withdrawal", json={"amount": "150001", "reason": "Exceso", "withdrawal_reason": "OTHER"}).status_code == 409
    assert client.post("/api/v1/cash/withdrawal", json={"amount": "0", "reason": "Inválido", "withdrawal_reason": "OTHER"}).status_code == 422


def test_withdrawal_requires_withdrawal_reason(cash_context):
    _, client, _, _ = cash_context
    open_cash(client)
    response = client.post("/api/v1/cash/withdrawal", json={"amount": "1000", "reason": "Sin motivo"})
    assert response.status_code == 422


def test_financial_movements_expose_withdrawal_reason(cash_context):
    """Section 7: a HOME withdrawal must be tellable from an OTHER one without parsing free text."""
    _, client, _, _ = cash_context
    open_cash(client)
    client.post("/api/v1/cash/withdrawal", json={"amount": "20000", "reason": "Mercado de la casa", "withdrawal_reason": "HOME"})
    client.post("/api/v1/cash/withdrawal", json={"amount": "5000", "reason": "Domicilio", "withdrawal_reason": "OTHER"})
    movements = client.get("/api/v1/cash/current").json()["financial_movements"]
    withdrawals = [m for m in movements if m["type"] == "WITHDRAWAL"]
    assert {(w["withdrawal_reason"], Decimal(w["amount"])) for w in withdrawals} == {("HOME", Decimal("20000")), ("OTHER", Decimal("5000"))}
    sale_or_none = [m["withdrawal_reason"] for m in movements if m["type"] != "WITHDRAWAL"]
    assert all(reason is None for reason in sale_or_none)


def test_expected_cash_includes_all_ledger_components(cash_context):
    _, client, _, _ = cash_context
    open_cash(client)
    client.post("/api/v1/cash/deposit", json={"amount": "50000", "reason": "Base"})
    client.post("/api/v1/cash/withdrawal", json={"amount": "30000", "reason": "Retiro", "withdrawal_reason": "OTHER"})
    client.post("/api/v1/cash/adjustment", json={"amount_delta": "-10000", "reason": "Ajuste"})
    summary = client.get("/api/v1/cash/current").json()
    assert Decimal(summary["expected_cash"]) == Decimal("210000")
    assert Decimal(summary["deposits"]) == Decimal("50000")
    assert Decimal(summary["withdrawals"]) == Decimal("30000")
    assert Decimal(summary["adjustments"]) == Decimal("-10000")


@pytest.mark.parametrize("counted,expected_difference", [
    ("200000", "0"), ("190000", "-10000"), ("210000", "10000")])
def test_close_balanced_short_and_over(cash_context, counted, expected_difference):
    _, client, _, _ = cash_context
    open_cash(client)
    body = {"counted_amount": counted, "expected_amount": "200000"}
    if Decimal(expected_difference):
        body["note"] = "Diferencia durante conteo"
    response = client.post("/api/v1/cash/close", json=body)
    assert response.status_code == 200
    assert response.json()["session"]["status"] == "CLOSED"
    assert Decimal(response.json()["session"]["difference"]) == Decimal(expected_difference)


def test_difference_requires_note(cash_context):
    _, client, _, _ = cash_context
    open_cash(client)
    assert client.post("/api/v1/cash/close", json={
        "counted_amount": "190000", "expected_amount": "200000"}).status_code == 422


def test_close_detects_stale_expected_rejects_double_close_and_closed_movements(cash_context):
    _, client, _, _ = cash_context
    open_cash(client)
    client.post("/api/v1/cash/deposit", json={"amount": "10000", "reason": "Mientras contaban"})
    stale = client.post("/api/v1/cash/close", json={
        "counted_amount": "200000", "expected_amount": "200000"})
    assert stale.status_code == 409
    closed = client.post("/api/v1/cash/close", json={
        "counted_amount": "210000", "expected_amount": "210000"})
    assert closed.status_code == 200
    assert client.post("/api/v1/cash/close", json={
        "counted_amount": "210000", "expected_amount": "210000"}).status_code == 409
    assert client.post("/api/v1/cash/deposit", json={"amount": "1", "reason": "Tarde"}).status_code == 409


def test_cash_closed_rejects_cash_and_digital(cash_context):
    db, client, product, channels = cash_context
    open_cash(client)
    client.post("/api/v1/cash/close", json={"counted_amount": "200000", "expected_amount": "200000"})
    response = client.post("/api/v1/sales")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OPERATIONAL_SESSION_CLOSED"


def test_mixed_cash_with_closed_session_rejected(cash_context):
    _, client, _, _ = cash_context
    response = client.post("/api/v1/sales")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OPERATIONAL_SESSION_CLOSED"


def test_double_checkout_does_not_duplicate_cash_movement(cash_context):
    db, client, product, channels = cash_context
    open_cash(client)
    sale = open_sale(db, product)
    body = {"channel_id": str(channels["Efectivo"].id), "received_amount": "100000"}
    assert client.post(f"/api/v1/sales/{sale.id}/checkout", json=body).status_code == 200
    assert client.post(f"/api/v1/sales/{sale.id}/checkout", json=body).status_code == 404
    assert db.scalar(select(func.count(CashMovement.id)).where(CashMovement.reference_id == sale.id)) == 1


def test_history_and_closed_detail_are_preserved(cash_context):
    _, client, _, _ = cash_context
    opened = open_cash(client).json()
    client.post("/api/v1/cash/close", json={"counted_amount": "200000", "expected_amount": "200000"})
    history = client.get("/api/v1/cash/history").json()
    assert len(history) == 1
    assert history[0]["session"]["status"] == "CLOSED"
    detail = client.get(f"/api/v1/cash/sessions/{opened['session']['id']}").json()
    assert Decimal(detail["session"]["closing_actual"]) == Decimal("200000")


def test_history_is_capped_most_recent_first(cash_context):
    """/cash/history never returns the full lifetime list — a `limit` (default 90) keeps it bounded."""
    _, client, _, _ = cash_context
    for i in range(3):
        client.post("/api/v1/cash/open", json={"opening_amount": "100000", "note": f"Turno {i}"})
        client.post("/api/v1/cash/close", json={"counted_amount": "100000", "expected_amount": "100000"})
    full = client.get("/api/v1/cash/history").json()
    assert len(full) == 3
    capped = client.get("/api/v1/cash/history", params={"limit": 2}).json()
    assert len(capped) == 2
    assert capped[0]["session"]["opening_note"] == "Turno 2"  # most recent first
    assert capped[1]["session"]["opening_note"] == "Turno 1"
