"""0028 on real PostgreSQL: CHECK / UNIQUE constraints of the patrimony and account tables, the valuation SQL,
the close transaction and concurrent balance registration.

Writes TEMPORARY fake data — run it ONLY against a disposable COPY of the database (DATABASE_URL), never the
real one. Skipped without PostgreSQL, and skipped if the copy already has an open cash session."""
import threading
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.domain.enums import InventoryMovementType, PaymentMethod
from app.models import (BusinessAccount, BusinessAccountMovement, BusinessAccountSnapshot, CashSession,
                        InventoryMovement, InventoryValuationSnapshot, PaymentChannel, PaymentCorrection, Product, User)
from app.schemas.cash import CloseCashSessionIn
from app.schemas.pos import CheckoutIn
from app.services import business_accounts, post_sale
from app.services.cash import close_cash_session, expected_cash
from app.services.inventory_valuation import compute_valuation
from app.services.pos import add_item, checkout, create_sale


@pytest.fixture()
def pg():
    if engine.dialect.name != "postgresql":
        pytest.skip("DATABASE_URL no apunta a PostgreSQL")
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("No hay una instancia de PostgreSQL alcanzable en DATABASE_URL")
    with Session(engine) as db:
        if db.scalar(select(CashSession).where(CashSession.closed_at.is_(None))) is not None:
            pytest.skip("La copia tiene una jornada abierta: no se toca")
        business_accounts.ensure_default_accounts(db); db.commit()
        user = db.scalar(select(User).limit(1))
        tag = uuid4().hex[:8]
        accounts = {a.code: a.id for a in db.scalars(select(BusinessAccount))}
        cash = PaymentChannel(name=f"PGTEST Efectivo {tag}", payment_type=PaymentMethod.CASH, affects_cash=True)
        nequi = PaymentChannel(name=f"PGTEST Nequi {tag}", payment_type=PaymentMethod.TRANSFER, affects_cash=False,
                               requires_destination_account=True, default_account_id=accounts["NEQUI"])
        breb = PaymentChannel(name=f"PGTEST Bre-B {tag}", payment_type=PaymentMethod.TRANSFER, affects_cash=False,
                              requires_destination_account=True)
        product = Product(barcode=f"PG{tag}", name=f"PGTEST {tag}", sale_price=Decimal("1000"), unit_cost=Decimal("500"))
        db.add_all([cash, nequi, breb, product]); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("100"), unit_cost=Decimal("500"), user_id=user.id))
        session = CashSession(opening_amount=Decimal("0"), opened_by=user.id, opened_at=datetime.now(timezone.utc))
        db.add(session); db.commit()
        yield {"product": product, "cash": cash, "nequi": nequi, "breb": breb, "session": session, "acc": accounts,
               "user": user}
        db.rollback()
        session = db.get(CashSession, session.id)
        if session.closed_at is None:
            session.closed_at = func.now(); session.closed_by = user.id     # leave no open jornada in the copy
            db.commit()


def sell(env, qty=2, channel="nequi", account="NEQUI"):
    with Session(engine) as db:
        sale = create_sale(db)
        add_item(db, sale.id, env["product"].barcode, Decimal(qty))
        ch = env[channel]
        body = CheckoutIn(channel_id=ch.id, received_amount=Decimal(1000 * qty) if ch.affects_cash else None,
                          destination_account_id=None if ch.affects_cash else env["acc"][account])
        sale = checkout(db, sale.id, body)
        return sale.id, sale.payments[0].id, sale.items[0].id


def close(env, balances=None):
    with Session(engine) as db:
        session = db.get(CashSession, env["session"].id)
        body = CloseCashSessionIn(counted_amount=expected_cash(db, session), expected_amount=expected_cash(db, session),
                                  account_balances=[{"account_id": env["acc"][c], "observed_balance": v}
                                                    for c, v in (balances or {}).items()] or None)
        return close_cash_session(db, body)


def test_valuation_sql_and_close_transaction_on_postgresql(pg):
    sell(pg, 2)
    with Session(engine) as db:
        live = compute_valuation(db)                        # FILTER / CASE aggregates are valid on PostgreSQL
        assert live["inventory_value"] >= Decimal("49000")
    close(pg, {"NEQUI": 2000})
    with Session(engine) as db:
        snapshot = db.scalar(select(InventoryValuationSnapshot).where(
            InventoryValuationSnapshot.cash_session_id == pg["session"].id))
        assert snapshot is not None and snapshot.inventory_value == live["inventory_value"]
        nequi = db.scalar(select(BusinessAccountSnapshot).where(BusinessAccountSnapshot.cash_session_id == pg["session"].id))
        assert nequi.observed_balance == Decimal("2000")


def test_a_failing_balance_rolls_back_the_whole_close(pg):
    with Session(engine) as db:
        session = db.get(CashSession, pg["session"].id)
        body = CloseCashSessionIn(counted_amount=expected_cash(db, session), expected_amount=expected_cash(db, session),
                                  account_balances=[{"account_id": uuid4(), "observed_balance": 1}])
        with pytest.raises(HTTPException):
            close_cash_session(db, body)
    with Session(engine) as db:
        assert db.get(CashSession, pg["session"].id).closed_at is None          # still open: unambiguous
        assert db.scalar(select(InventoryValuationSnapshot).where(
            InventoryValuationSnapshot.cash_session_id == pg["session"].id)) is None


def test_database_refuses_duplicate_and_incoherent_snapshots(pg):
    close(pg)
    with Session(engine) as db:
        db.add(InventoryValuationSnapshot(cash_session_id=pg["session"].id, inventory_value=1, products_with_positive_stock=0,
                                          products_valued=0, products_missing_cost=0, products_negative_stock=0,
                                          quantity_valued=0, quantity_missing_cost=0, cost_source="X"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        now = datetime.now(timezone.utc)
        db.add(BusinessAccountSnapshot(cash_session_id=pg["session"].id, business_account_id=pg["acc"]["NU"],
                                       observed_balance=10, is_baseline=True, pos_inflows=0, pos_outflows=0,
                                       registered_external_inflows=0, registered_external_outflows=0,
                                       expected_balance=5, difference=5, window_start=now, window_end=now,
                                       user_id=pg["user"].id))             # a baseline cannot carry a difference
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_movement_reason_check_and_account_only_correction_check(pg):
    with Session(engine) as db:
        db.add(BusinessAccountMovement(business_account_id=pg["acc"]["NU"], cash_session_id=pg["session"].id,
                                       direction="OUT", amount=1, reason="OTHER_IN", idempotency_key=uuid4().hex,
                                       user_id=pg["user"].id))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    sale_id, payment_id, _ = sell(pg, 1)
    with Session(engine) as db:                                   # same channel, another account: accepted
        correction = post_sale.correct_payment(db, sale_id, payment_id, to_channel_id=pg["nequi"].id, reason="pg",
                                               idempotency_key=f"pg-{uuid4().hex}", to_account_id=pg["acc"]["NU"])
        assert correction.from_channel_id == correction.to_channel_id and correction.to_account_id == pg["acc"]["NU"]
    with Session(engine) as db:                                   # neither channel nor account changes: refused
        db.add(PaymentCorrection(sale_id=sale_id, payment_id=payment_id, sequence=99, from_channel_id=pg["nequi"].id,
                                 to_channel_id=pg["nequi"].id, from_account_id=pg["acc"]["NU"],
                                 to_account_id=pg["acc"]["NU"], amount=1, reason="x", user_id=pg["user"].id,
                                 idempotency_key=uuid4().hex))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_concurrent_balance_registration_creates_one_snapshot(pg):
    close(pg)
    results = []

    def register(value):
        try:
            with Session(engine) as db:
                business_accounts.register_balances_after_close(
                    db, pg["session"].id, [{"account_id": pg["acc"]["DAVIVIENDA"], "observed_balance": value}])
                results.append("ok")
        except HTTPException as exc:
            results.append(exc.status_code)
        except Exception as exc:  # noqa: BLE001 - any database error fails the test
            results.append(repr(exc))

    threads = [threading.Thread(target=register, args=(v,)) for v in (100, 200, 300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(map(str, results)) == ["409", "409", "ok"], results
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BusinessAccountSnapshot).where(
            BusinessAccountSnapshot.cash_session_id == pg["session"].id,
            BusinessAccountSnapshot.business_account_id == pg["acc"]["DAVIVIENDA"])) == 1
