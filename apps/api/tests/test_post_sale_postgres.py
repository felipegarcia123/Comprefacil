"""POST-SALE 0027 on real PostgreSQL: row locks, unique partial index, concurrent returns / VOID / corrections.

Writes TEMPORARY fake data (a product, two channels, a cash session, sales) — run it ONLY against a disposable
COPY of the database (DATABASE_URL), never the real one; the copy is dropped afterwards. Skipped without
PostgreSQL, and skipped if the copy already has an open cash session (it never touches a real jornada)."""
import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.domain.enums import CashMovementType, InventoryMovementType, PaymentMethod
from app.models import (CashMovement, CashSession, InventoryMovement, Payment, PaymentChannel, PaymentCorrection,
                        Product, Sale, SaleRefund, SaleReturn, User)
from app.schemas.pos import CheckoutIn
from app.services import post_sale
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
        user = db.scalar(select(User).limit(1))
        tag = uuid4().hex[:8]
        cash = PaymentChannel(name=f"PGTEST Efectivo {tag}", payment_type=PaymentMethod.CASH, affects_cash=True)
        nequi = PaymentChannel(name=f"PGTEST Nequi {tag}", payment_type=PaymentMethod.TRANSFER, affects_cash=False)
        product = Product(barcode=f"PG{tag}", name=f"PGTEST {tag}", sale_price=Decimal("1000"), unit_cost=Decimal("500"))
        db.add_all([cash, nequi, product]); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("100"), unit_cost=Decimal("500"), user_id=user.id))
        session = CashSession(opening_amount=Decimal("0"), opened_by=user.id)
        db.add(session); db.commit()
        yield {"product": product, "cash": cash, "nequi": nequi, "session": session}
        db.rollback()
        session = db.get(CashSession, session.id)
        session.closed_at = func.now(); session.closed_by = user.id     # leave no open jornada in the copy
        db.commit()


def sell(env, qty=3, channel="cash"):
    with Session(engine) as db:
        sale = create_sale(db)
        add_item(db, sale.id, env["product"].barcode, Decimal(qty))
        ch = env[channel]
        body = CheckoutIn(channel_id=ch.id, received_amount=Decimal(1000 * qty) if ch.affects_cash else None)
        sale = checkout(db, sale.id, body)
        return sale.id, sale.payments[0].id, sale.items[0].id


def race(workers):
    """Hold the sale row lock, start every worker (they must block on it), then release: all of them
    really compete for the same row at the same instant."""
    def run(sale_id, fn, results, index):
        try:
            with Session(engine) as db:
                results[index] = fn(db)
        except HTTPException as exc:
            results[index] = exc.status_code
        except Exception as exc:  # noqa: BLE001 - surfaced by the asserts
            results[index] = repr(exc)
    sale_id, fns = workers
    results = [None] * len(fns)
    with Session(engine) as holder:
        holder.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
        threads = [threading.Thread(target=run, args=(sale_id, fn, results, i)) for i, fn in enumerate(fns)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(0.5)
        assert all(t.is_alive() for t in threads)            # blocked on the row lock
        holder.rollback()
    for t in threads:
        t.join(10)
    return results


def test_concurrent_returns_never_exceed_sold_quantity(pg):
    sale_id, _, item_id = sell(pg, 3)
    fn = lambda db: post_sale.create_return(db, sale_id, lines=[{"sale_item_id": item_id, "quantity": 2, "restock": True}],
                                            reason_code="CUSTOMER_RETURN", reason_text=None, refund_channel_id=None,
                                            idempotency_key=f"pg-{uuid4().hex}").id
    results = race((sale_id, [fn, fn, fn]))
    assert sorted(type(r).__name__ for r in results) == ["UUID", "int", "int"] and results.count(409) == 2
    with Session(engine) as db:
        assert db.scalar(select(func.sum(InventoryMovement.quantity)).where(
            InventoryMovement.movement_type == InventoryMovementType.RETURN,
            InventoryMovement.reference_id.in_(select(SaleReturn.id).where(SaleReturn.sale_id == sale_id)))) == 2
        assert db.scalar(select(func.sum(CashMovement.amount)).where(
            CashMovement.reference_id == sale_id, CashMovement.movement_type == CashMovementType.REFUND)) == -2000


def test_concurrent_voids_create_one_and_same_key_is_idempotent(pg):
    sale_id, _, _ = sell(pg, 2)
    same = f"pg-{uuid4().hex}"
    void = lambda key: (lambda db: post_sale.void_sale(db, sale_id, reason_code="WRONG_CHARGE", reason_text=None,
                                                       restock=True, idempotency_key=key).id)
    results = race((sale_id, [void(same), void(same), void(f"pg-{uuid4().hex}")]))
    ids = [r for r in results if not isinstance(r, int)]
    assert len(ids) == 2 and ids[0] == ids[1] and results.count(409) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(SaleReturn).where(SaleReturn.sale_id == sale_id)) == 1
        assert db.scalar(select(func.count()).select_from(CashMovement).where(
            CashMovement.reference_id == sale_id, CashMovement.movement_type == CashMovementType.REFUND)) == 1


def test_concurrent_void_and_return_one_wins(pg):
    sale_id, _, item_id = sell(pg, 2)
    void = lambda db: post_sale.void_sale(db, sale_id, reason_code="WRONG_CHARGE", reason_text=None, restock=True,
                                          idempotency_key=f"pg-{uuid4().hex}").kind
    ret = lambda db: post_sale.create_return(db, sale_id, lines=[{"sale_item_id": item_id, "quantity": 1, "restock": True}],
                                             reason_code="CUSTOMER_RETURN", reason_text=None, refund_channel_id=None,
                                             idempotency_key=f"pg-{uuid4().hex}").kind
    results = race((sale_id, [void, ret]))
    assert results.count(409) == 1
    with Session(engine) as db:
        returned = db.scalar(select(func.coalesce(func.sum(SaleReturn.total_amount), 0)).where(SaleReturn.sale_id == sale_id))
        assert returned <= 2000


def test_concurrent_corrections_keep_a_clean_sequence_and_cash(pg):
    sale_id, payment_id, _ = sell(pg, 1, "cash")
    to = lambda channel: (lambda db: post_sale.correct_payment(db, sale_id, payment_id, to_channel_id=pg[channel].id,
                                                               reason="pg", idempotency_key=f"pg-{uuid4().hex}").sequence)
    results = race((sale_id, [to("nequi"), to("nequi"), to("cash")]))
    # Every worker ends in a sequence number or an HTTP refusal: never a database error (e.g. a CHECK
    # constraint that does not know PAYMENT_CORRECTION — the bug the visual test on a copy caught).
    assert all(isinstance(r, int) for r in results), results
    with Session(engine) as db:
        chain = db.scalars(select(PaymentCorrection).where(PaymentCorrection.payment_id == payment_id)
                           .order_by(PaymentCorrection.sequence)).all()
        assert [c.sequence for c in chain] == list(range(1, len(chain) + 1))
        for previous, current in zip(chain, chain[1:]):
            assert current.from_channel_id == previous.to_channel_id          # each one starts where the last ended
        drawer = db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0)).where(CashMovement.reference_id == sale_id))
        effective_is_cash = (chain[-1].to_channel_id if chain else pg["cash"].id) == pg["cash"].id
        assert drawer == (Decimal("1000") if effective_is_cash else Decimal("0"))
    # Each accepted correction got its own sequence; a refused one ("already that channel") is a 409.
    assert sorted(r for r in results if r != 409) == [c.sequence for c in chain]


def test_database_refuses_a_second_void_row(pg):
    sale_id, _, _ = sell(pg, 1)
    with Session(engine) as db:
        user = db.scalar(select(User).limit(1))
        for _ in range(2):
            db.add(SaleReturn(sale_id=sale_id, kind="VOID", reason_code="OTHER", total_amount=Decimal("0"),
                              cash_session_id=pg["session"].id, user_id=user.id, idempotency_key=f"pg-{uuid4().hex}"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_refund_lock_and_detail_queries_are_valid_sql(pg):
    sale_id, _, item_id = sell(pg, 1, "nequi")
    with Session(engine) as db:
        sale_return = post_sale.create_return(db, sale_id, lines=[{"sale_item_id": item_id, "quantity": 1, "restock": True}],
                                              reason_code="CUSTOMER_RETURN", reason_text=None, refund_channel_id=None,
                                              idempotency_key=f"pg-{uuid4().hex}")
        refund_id = sale_return.refunds[0].id
    results = []
    def confirm():
        with Session(engine) as db:
            results.append(post_sale.confirm_refund(db, sale_id, refund_id).confirmed_at)
    threads = [threading.Thread(target=confirm) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(set(results)) == 1                                                 # confirmed exactly once
    with Session(engine) as db:
        detail = post_sale.sale_detail(db, sale_id)
        assert detail["refund_status"] == "COMPLETED" and detail["display_status"] == "RETURNED"
        assert db.get(SaleRefund, refund_id).status == "COMPLETED"


def test_cash_moving_correction_is_accepted_by_the_database(pg):
    """The 0027 migration widens ck_cash_movements_type: a cash -> electronic correction stores its
    PAYMENT_CORRECTION movement on real PostgreSQL (SQLite never had that CHECK)."""
    sale_id, payment_id, _ = sell(pg, 1, "cash")
    with Session(engine) as db:
        correction = post_sale.correct_payment(db, sale_id, payment_id, to_channel_id=pg["nequi"].id, reason="pg",
                                               idempotency_key=f"pg-{uuid4().hex}")
        movement = db.get(CashMovement, correction.cash_movement_id)
        assert movement.movement_type == CashMovementType.PAYMENT_CORRECTION and movement.amount == Decimal("-1000")


def _replacement(pg, price="1000"):
    """A second test product for exchanges (same price as the sold one unless told otherwise)."""
    with Session(engine) as db:
        user = db.scalar(select(User).limit(1))
        other = Product(barcode=f"PGX{uuid4().hex[:9]}", name=f"PGTEST cambio {uuid4().hex[:6]}",
                        sale_price=Decimal(price), unit_cost=Decimal("400"))
        db.add(other); db.flush()
        db.add(InventoryMovement(product_id=other.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("10"), unit_cost=Decimal("400"), user_id=user.id))
        db.commit()
        return other.id


def test_concurrent_exchanges_of_the_same_unit_create_one(pg):
    sale_id, _, item_id = sell(pg, 1)
    other = _replacement(pg)
    fn = lambda db: str(post_sale.create_exchange(
        db, sale_id, return_lines=[{"sale_item_id": item_id, "quantity": 1, "restock": True}],
        replacement_lines=[{"product_id": other, "quantity": 1}], reason_code="CHANGED_MIND", reason_text=None,
        settlement=None, idempotency_key=f"pg-{uuid4().hex}").id)
    results = race((sale_id, [fn, fn, fn]))
    assert sum(isinstance(r, str) and not r.startswith("<") and len(r) == 36 for r in results) == 1, results
    assert results.count(409) == 2
    with Session(engine) as db:
        assert db.scalar(select(func.sum(InventoryMovement.quantity)).where(
            InventoryMovement.product_id == other, InventoryMovement.movement_type == InventoryMovementType.EXCHANGE_OUT)) == -1


def test_exchange_with_cash_difference_is_accepted_by_the_real_checks(pg):
    """EXCHANGE_OUT (inventory) and EXCHANGE (cash) pass the widened CHECK constraints of 0027."""
    sale_id, _, item_id = sell(pg, 1)
    other = _replacement(pg, price="1500")
    with Session(engine) as db:
        exchange = post_sale.create_exchange(
            db, sale_id, return_lines=[{"sale_item_id": item_id, "quantity": 1, "restock": True}],
            replacement_lines=[{"product_id": other, "quantity": 1}], reason_code="WRONG_SIZE", reason_text=None,
            settlement={"channel_id": pg["cash"].id, "received_amount": 500}, idempotency_key=f"pg-{uuid4().hex}")
        movement = db.get(CashMovement, exchange.money.cash_movement_id)
        assert movement.movement_type == CashMovementType.EXCHANGE and movement.amount == Decimal("500")


def test_database_refuses_an_inconsistent_settlement(pg):
    """Even bypassing the service, PostgreSQL rejects a settlement that does not match the signed balance."""
    from app.models import SaleExchange
    sale_id, _, _ = sell(pg, 1)
    with Session(engine) as db:
        user = db.scalar(select(User).limit(1))
        db.add(SaleExchange(sale_id=sale_id, reason_code="OTHER", returned_credit=Decimal("1000"),
                            replacement_total=Decimal("800"), difference=Decimal("-200"), settlement="EVEN",
                            status="COMPLETED", cash_session_id=pg["session"].id, user_id=user.id,
                            idempotency_key=f"pg-{uuid4().hex}"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_store_refund_in_cash_is_accepted_by_the_real_checks(pg):
    """STORE_REFUNDS in cash: CashMovement EXCHANGE_REFUND passes the widened CHECK; only the difference."""
    sale_id, _, item_id = sell(pg, 1)
    cheaper = _replacement(pg, price="600")
    with Session(engine) as db:
        exchange = post_sale.create_exchange(
            db, sale_id, return_lines=[{"sale_item_id": item_id, "quantity": 1, "restock": True}],
            replacement_lines=[{"product_id": cheaper, "quantity": 1}], reason_code="WRONG_ITEM", reason_text=None,
            settlement={"channel_id": pg["cash"].id}, idempotency_key=f"pg-{uuid4().hex}")
        movement = db.get(CashMovement, exchange.money.cash_movement_id)
        assert exchange.settlement == "STORE_REFUNDS"
        assert movement.movement_type == CashMovementType.EXCHANGE_REFUND and movement.amount == Decimal("-400")


def test_concurrent_chained_exchanges_of_the_same_delivered_unit_create_one(pg):
    """Two registers try to exchange the SAME unit delivered by an earlier exchange: exactly one wins."""
    sale_id, _, item_id = sell(pg, 1)
    first, second = _replacement(pg), _replacement(pg)
    with Session(engine) as db:
        delivered = post_sale.create_exchange(
            db, sale_id, return_lines=[{"sale_item_id": item_id, "quantity": 1, "restock": True}],
            replacement_lines=[{"product_id": first, "quantity": 1}], reason_code="WRONG_SIZE", reason_text=None,
            settlement=None, idempotency_key=f"pg-{uuid4().hex}").replacements[0].id
    fn = lambda db: str(post_sale.create_exchange(
        db, sale_id, return_lines=[{"replacement_line_id": delivered, "quantity": 1, "restock": True}],
        replacement_lines=[{"product_id": second, "quantity": 1}], reason_code="WRONG_SIZE", reason_text=None,
        settlement=None, idempotency_key=f"pg-{uuid4().hex}").id)
    results = race((sale_id, [fn, fn, fn]))
    assert sum(isinstance(r, str) and len(r) == 36 for r in results) == 1, results
    assert results.count(409) == 2
    with Session(engine) as db:
        assert db.scalar(select(func.sum(InventoryMovement.quantity)).where(
            InventoryMovement.product_id == second,
            InventoryMovement.movement_type == InventoryMovementType.EXCHANGE_OUT)) == -1
