"""POS <-> Alegra catalog sync: statuses, LINK (dry-run / real / idempotent / conflicts),
CREATE behind three barriers, batch limit, stable pagination and remote idempotency.
Fake Alegra only (the real account is unreachable: invalid URL)."""
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.main import app
from app.models import CatalogSyncOperation, CatalogSyncRun, Product
from app.services import alegra_catalog_sync as sync
from app.services.alegra_client import AlegraError, AlegraNetworkError, AlegraValidationError
from app.services.alegra_item_mapping import fetch_all_items

API = "/api/v1/alegra-catalog"


def item(id_, reference, name="X", status="active"):
    return {"id": str(id_), "reference": reference, "name": name, "status": status}


class FakeClient:
    def __init__(self, alegra):
        self.alegra = alegra

    def get(self, path, params=None):
        self.alegra.gets += 1
        if self.alegra.get_error:
            raise self.alegra.get_error
        if path.startswith("/items/"):
            return next((i for i in self.alegra.items if i["id"] == path.rsplit("/", 1)[1]), None)
        ref = (params or {}).get("reference", "")
        return [i for i in self.alegra.items if ref in (i.get("reference") or "")]      # SUBSTRING like Alegra


class FakeAlegra:
    def __init__(self, items):
        self.items = list(items)
        self.gets = 0
        self.get_error = None
        self.created = []
        self.create_mode = "ok"               # ok | timeout_after | timeout_before | reject
        self.client = FakeClient(self)

    def list_items_page(self, start=0, limit=30):
        return self.items[start:start + limit], len(self.items)

    def create_item(self, payload):
        if self.create_mode == "reject":
            raise AlegraValidationError("nombre inválido")
        if self.create_mode == "timeout_before":
            raise AlegraNetworkError("timeout")
        new = {"id": str(9000 + len(self.created)), "reference": payload["reference"], "name": payload["name"],
               "status": "active"}
        self.created.append(payload); self.items.append(new)
        if self.create_mode == "timeout_after":
            raise AlegraNetworkError("timeout")
        return new


@pytest.fixture()
def ctx(monkeypatch):
    from app.core.config import settings
    for key, value in {"alegra_api_url": "http://alegra.invalid", "alegra_catalog_create_enabled": False,
                       "alegra_catalog_batch_limit": 50, "alegra_enabled": True, "alegra_email": "a@b.co",
                       "alegra_token": "t"}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        p = lambda code, name, price="1000", mapped=None, active=True: Product(
            barcode=code, name=name, sale_price=Decimal(price), unit_cost=Decimal(1), alegra_item_id=mapped, active=active)
        prods = {
            "linked": p("7702177022504", "LECHE", mapped="287"),
            "exact": p("7702191523995", "PAN"),
            "upc": p("012345678905", "TWIN"),
            "new": p("4005900036759", "CREMA NUEVA", "5200"),
            "nobarcode": p(None, "HUEVO AA X UND", "550"),
            "invalid": p("COL-9", "INTERNO"),
            "conflict_a": p("7501031311309", "ARENA A"),
            "zero": p("7702354956837", "SIN PRECIO", "0"),
            "samename": p("7707211440016", "AGUA CRISTAL X 600 ML"),
            "zeros": p("7702028317148", "ACEITE"),
            "dead": p("7707430875408", "HUERFANO", mapped="555"),
        }
        db.add_all(prods.values()); db.commit()
        alegra = FakeAlegra([
            item(287, "7702177022504", "LECHE"), item(10, "7702191523995", "PAN"), item(11, "0012345678905", "TWIN"),
            item(12, "7501031311309", "ARENA"), item(13, "90100111710111", "HUEVO AA X UND"),
            item(14, "123", "AGUA CRISTAL X 600 ML"), item(15, "07702028317148", "ACEITE ZEROS"),
        ])
        monkeypatch.setattr(sync, "AlegraProvider", lambda: alegra)
        def override(): yield db
        app.dependency_overrides[get_db] = override
        try:
            yield db, alegra, prods, settings, factory
        finally:
            app.dependency_overrides.clear()


def statuses(db, alegra):
    result = sync.preview_all(db, sync.build_index(alegra.items))
    return {r["product_name"]: r for r in result["rows"]}, result


def test_every_status_and_no_name_match(ctx):
    db, alegra, prods, *_ = ctx
    rows, result = statuses(db, alegra)
    assert rows["LECHE"]["sync_status"] == "ALREADY_LINKED" and rows["LECHE"]["proposed_action"] == "NONE"
    assert rows["PAN"]["sync_status"] == "EXACT_MATCH" and rows["PAN"]["confidence"] == "HIGH"
    assert rows["TWIN"]["sync_status"] == "UPC_EAN_MATCH" and rows["TWIN"]["matched_alegra_item_id"] == "11"
    assert rows["CREMA NUEVA"]["sync_status"] == "READY_TO_CREATE" and rows["CREMA NUEVA"]["proposed_action"] == "CREATE"
    no_code = rows["HUEVO AA X UND"]
    assert no_code["sync_status"] == "NO_BARCODE" and no_code["matched_alegra_item_id"] is None   # name is a hint only
    assert "13" in no_code["review_hint"]
    assert rows["INTERNO"]["sync_status"] == "INVALID_BARCODE"
    assert rows["SIN PRECIO"]["sync_status"] == "REVIEW_REQUIRED"
    assert rows["AGUA CRISTAL X 600 ML"]["sync_status"] == "REVIEW_REQUIRED"          # same name, other code
    assert rows["ACEITE"]["sync_status"] == "REVIEW_REQUIRED" and "ceros" in rows["ACEITE"]["reason"]
    assert rows["HUERFANO"]["sync_status"] == "REVIEW_REQUIRED"                        # linked item no longer exists
    assert result["summary"]["exact_match"] == 3 and result["summary"]["no_barcode"] == 1


def test_two_products_claiming_one_item_are_conflicts(ctx):
    """TWIN (012345678905, UPC twin) and a second product with the exact 13-digit code both
    point at Alegra item 11: neither is linked automatically."""
    db, alegra, prods, *_ = ctx
    db.add(Product(barcode="0012345678905", name="TWIN EAN", sale_price=Decimal(1), unit_cost=Decimal(1)))
    db.commit()
    rows, result = statuses(db, alegra)
    assert rows["TWIN"]["sync_status"] == "CONFLICT" and rows["TWIN EAN"]["sync_status"] == "CONFLICT"
    assert rows["TWIN"]["proposed_action"] == "REVIEW" and result["summary"]["conflicts"] == 2


def test_item_already_held_by_another_product_is_conflict(ctx):
    db, alegra, prods, *_ = ctx
    prods["linked"].alegra_item_id = "10"; db.commit()     # LECHE (wrongly) holds PAN's item
    rows, _ = statuses(db, alegra)
    assert rows["PAN"]["sync_status"] == "CONFLICT"


def test_stable_pagination_and_total_mismatch_aborts():
    class Shifting:
        def list_items_page(self, start=0, limit=30):
            ids = list(range(start, start + 30)) if start == 0 else [29] + list(range(start + 1, start + 30))
            return [{"id": str(i), "reference": str(i)} for i in ids][: max(0, 45 - start)], 45
    with pytest.raises(AlegraError):
        fetch_all_items(Shifting(), pause=0)


def test_link_dry_run_writes_nothing_then_real_link_is_audited_and_idempotent(ctx):
    db, alegra, prods, *_ = ctx
    pid = prods["exact"].id
    dry = sync.link_existing(db, pid, "10", alegra, dry_run=True)
    assert dry["status"] == "EXACT_MATCH" and db.get(Product, pid).alegra_item_id is None
    assert db.scalar(select(func.count()).select_from(CatalogSyncOperation)) == 0      # previews never audited
    real = sync.link_existing(db, pid, "10", alegra, dry_run=False)
    assert real["status"] == "LINKED" and db.get(Product, pid).alegra_item_id == "10"
    op = db.scalar(select(CatalogSyncOperation))
    assert (op.action, op.status, op.old_alegra_item_id, op.new_alegra_item_id, op.reference) == \
        ("LINK", "LINKED", None, "10", "7702191523995")
    again = sync.link_existing(db, pid, "10", alegra, dry_run=False)
    assert again["status"] == "ALREADY_LINKED"
    assert db.scalar(select(func.count()).select_from(CatalogSyncOperation)) == 1


def test_link_revalidates_live_before_writing(ctx):
    db, alegra, prods, *_ = ctx
    pid = prods["exact"].id
    next(i for i in alegra.items if i["id"] == "10")["reference"] = "999"          # changed in Alegra meanwhile
    assert sync.link_existing(db, pid, "10", alegra, dry_run=False)["status"] == "REVIEW_REQUIRED"
    alegra.items = [i for i in alegra.items if i["id"] != "10"]                     # deleted in Alegra
    assert sync.link_existing(db, pid, "10", alegra, dry_run=False)["status"] == "REVIEW_REQUIRED"
    alegra.get_error = AlegraNetworkError("caído")
    assert sync.link_existing(db, pid, "10", alegra, dry_run=False)["status"] == "ERROR"
    assert db.get(Product, pid).alegra_item_id is None


def test_link_conflicts_other_product_or_other_item(ctx):
    db, alegra, prods, *_ = ctx
    result = sync.link_existing(db, prods["linked"].id, "10", alegra, dry_run=False)     # already has 287
    assert result["status"] == "CONFLICT" and db.get(Product, prods["linked"].id).alegra_item_id == "287"
    prods["upc"].alegra_item_id = "10"; db.commit()
    assert sync.link_existing(db, prods["exact"].id, "10", alegra, dry_run=False)["status"] == "CONFLICT"
    assert {o.status for o in db.scalars(select(CatalogSyncOperation))} == {"CONFLICT"}


def test_concurrent_link_is_stopped_by_the_unique_index(ctx):
    db, alegra, prods, _, factory = ctx
    # Another session links item 10 to TWIN right after our checks: the DB refuses ours.
    real_get = alegra.client.get
    def racing_get(path, params=None):
        response = real_get(path, params)
        if path == "/items/10":
            with factory() as other:
                other.get(Product, prods["upc"].id).alegra_item_id = "10"; other.commit()
        return response
    alegra.client.get = racing_get
    result = sync.link_existing(db, prods["exact"].id, "10", alegra, dry_run=False)
    assert result["status"] == "CONFLICT"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Product).where(Product.alegra_item_id == "10")) == 1


def test_no_barcode_never_links_by_name_and_creates_with_internal_reference(ctx):
    db, alegra, prods, *_ = ctx
    product = prods["nobarcode"]                    # Alegra has item 13 with the SAME name: never a match
    assert sync.link_existing(db, product.id, "13", alegra, dry_run=False)["status"] == "NO_BARCODE"
    result = sync.create_remote(db, product.id, alegra, dry_run=False)
    assert result["status"] == "READY_TO_CREATE" and alegra.created == []
    assert result["payload"]["reference"] == f"POS-{product.id.hex[:16].upper()}"


def test_create_blocked_by_flag_and_dry_run_never_posts(ctx):
    db, alegra, prods, settings, _ = ctx
    pid = prods["new"].id
    for dry_run in (True, False):
        result = sync.create_remote(db, pid, alegra, dry_run=dry_run)
        assert result["status"] == "READY_TO_CREATE" and alegra.created == []
    assert result["payload"] == {"name": "CREMA NUEVA", "reference": "4005900036759", "type": "product",
                                 "price": [{"idPriceList": "1", "price": 5200.0}], "tax": [], "category": {"id": "5148"},
                                 "inventory": {"unit": "unit"}}
    assert result["payload"]["inventory"] == {"unit": "unit"}          # unit only: never quantities/costs
    monkey_enabled = settings.alegra_catalog_create_enabled
    assert monkey_enabled is False


def test_create_when_enabled_searches_first_and_links_if_it_exists(ctx, monkeypatch):
    db, alegra, prods, settings, _ = ctx
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", True)
    alegra.items.append(item(20, "4005900036759", "CREMA YA EXISTE"))
    result = sync.create_remote(db, prods["new"].id, alegra, dry_run=False)
    assert result["status"] == "LINKED" and alegra.created == []


def test_create_ambiguous_answer_rechecks_instead_of_reposting(ctx, monkeypatch):
    db, alegra, prods, settings, _ = ctx
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", True)
    alegra.create_mode = "timeout_after"                     # created, answer lost
    result = sync.create_remote(db, prods["new"].id, alegra, dry_run=False)
    assert result["status"] == "LINKED" and len(alegra.created) == 1
    assert db.get(Product, prods["new"].id).alegra_item_id == "9000"


def test_create_ambiguous_and_search_fails_is_pending_not_retry(ctx, monkeypatch):
    db, alegra, prods, settings, _ = ctx
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", True)
    alegra.create_mode = "timeout_before"
    real_get = alegra.client.get
    calls = {"n": 0}
    def flaky(path, params=None):
        calls["n"] += 1
        if calls["n"] > 1:                                   # the post-timeout re-search fails
            raise AlegraNetworkError("sin red")
        return real_get(path, params)
    alegra.client.get = flaky
    result = sync.create_remote(db, prods["new"].id, alegra, dry_run=False)
    assert result["status"] == "PENDING" and alegra.created == [] and db.get(Product, prods["new"].id).alegra_item_id is None


def test_create_success_when_enabled_is_audited(ctx, monkeypatch):
    db, alegra, prods, settings, _ = ctx
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", True)
    result = sync.create_remote(db, prods["new"].id, alegra, dry_run=False)
    assert result["status"] == "CREATED" and len(alegra.created) == 1
    assert db.scalar(select(CatalogSyncOperation)).action == "CREATE"


# ---- API: barriers the frontend cannot skip -----------------------------------------------------------------

def run_analysis(db, alegra):
    run = sync.start_run(db)
    return sync.execute_run(db, run.id, alegra)


def test_analysis_is_stored_and_listed(ctx):
    db, alegra, prods, *_ = ctx
    run = run_analysis(db, alegra)
    assert run.status == "DONE" and run.remote_items == 7
    body = TestClient(app).get(f"{API}/analysis/latest", params={"status": "EXACT_MATCH"}).json()
    assert body["total"] == 2 and {r["product_name"] for r in body["rows"]} == {"PAN", "ARENA A"}
    assert body["summary"]["active"] == 11


def test_incomplete_catalog_fails_the_run(ctx):
    db, alegra, *_ = ctx
    class Broken(FakeAlegra):
        def list_items_page(self, start=0, limit=30):
            return self.items[:3], 99
    run = sync.start_run(db)
    assert sync.execute_run(db, run.id, Broken(alegra.items)).status == "FAILED"


def test_api_create_real_is_refused_while_disabled(ctx):
    db, alegra, prods, *_ = ctx
    client = TestClient(app)
    refused = client.post(f"{API}/create", json={"product_ids": [str(prods["new"].id)], "dry_run": False})
    assert refused.status_code == 403 and alegra.created == []
    dry = client.post(f"{API}/create", json={"product_ids": [str(prods["new"].id)]}).json()   # dry_run defaults true
    assert dry["results"][0]["status"] == "READY_TO_CREATE" and alegra.created == []


def test_api_batch_limit(ctx, monkeypatch):
    db, alegra, prods, settings, _ = ctx
    monkeypatch.setattr(settings, "alegra_catalog_batch_limit", 2)
    ids = [str(p.id) for p in list(prods.values())[:3]]
    assert TestClient(app).post(f"{API}/link", json={"product_ids": ids}).status_code == 422


def test_api_link_only_unambiguous_rows_from_last_analysis(ctx):
    db, alegra, prods, *_ = ctx
    run_analysis(db, alegra)
    client = TestClient(app)
    body = client.post(f"{API}/link", json={"product_ids": [str(prods["exact"].id), str(prods["nobarcode"].id),
                                                            str(prods["upc"].id)], "dry_run": False}).json()
    by = {r["product_id"]: r["status"] for r in body["results"]}
    assert by[str(prods["exact"].id)] == "LINKED" and by[str(prods["upc"].id)] == "LINKED"
    assert by[str(prods["nobarcode"].id)] == "REVIEW_REQUIRED"          # not a LINK row: never linked by name
    ops = client.get(f"{API}/operations").json()
    assert {o["status"] for o in ops} == {"LINKED"} and len(ops) == 2


def test_priority_products_preview_has_definitive_values(ctx):
    db, *_ = ctx
    rows = TestClient(app).get(f"{API}/priority-products").json()
    assert [r["alegra_item_id"] for r in rows] == ["98", "27", "46", "198", "1621", "1659", "150"]
    assert [(r["unit_cost"], r["sale_price"]) for r in rows] == [(600, 700), (300, 1000), (17000, 18000), (1950, 3000),
                                                                  (1200, 1300), (800, 900), (1400, 2000)]
    assert all(r["barcode"] is None and r["status"] == "READY_TO_CREATE_LOCAL" for r in rows)


# ---- reverse flow: existing Alegra item -> new local Product ----------------------------------------------

REMOTE_7 = [item(98, "90100111710111", "HUEVO AA X UND"), item(27, "90100111720219", "AREPA SIN SAL"),
            item(46, "90100111710108", "HUEVO AA X 30 UND"), item(198, "90100111710203", "SALCHICHA MANGUERA"),
            item(1621, "90100112010165", "CIGARRILLO ROTHMANS AZUL"), item(1659, "90100111730107", "CLAVOS DE OLOR"),
            item(150, "90100112010161", "CIGARRILLO MARLBORO")]


@pytest.fixture()
def reverse(ctx):
    db, alegra, prods, settings, factory = ctx
    from app.models import OperationalCategory, User
    db.add_all([User(name="Cajero"), OperationalCategory(code="SUPER", name="Super"),
                OperationalCategory(code="CIGARROS", name="Cigarros")])
    db.commit()
    alegra.items += REMOTE_7
    # the base fixture has a local "HUEVO AA X UND" (no-barcode case); here it must not exist yet
    db.scalar(select(Product).where(Product.name == "HUEVO AA X UND")).name = "HUEVO FIXTURE"
    db.commit()
    alegra.create_item = lambda payload: (_ for _ in ()).throw(AssertionError("no debe crear en Alegra"))
    return db, alegra


def test_reverse_creates_seven_linked_products_without_barcode_or_stock(reverse):
    db, alegra = reverse
    from app.models import InventoryMovement, OperationalCategory
    dry = sync.create_pos_products_for_alegra_items(db, sync.PRIORITY_POS_PRODUCTS, alegra, dry_run=True)
    assert dry["status"] == "VALIDATED" and db.scalar(select(func.count()).select_from(Product).where(
        Product.alegra_item_id == "98")) == 0
    result = sync.create_pos_products_for_alegra_items(db, sync.PRIORITY_POS_PRODUCTS, alegra, dry_run=False)
    assert result["status"] == "CREATED" and len(result["created"]) == 7
    for spec in sync.PRIORITY_POS_PRODUCTS:
        product = db.scalar(select(Product).where(Product.alegra_item_id == spec["alegra_item_id"]))
        category = db.get(OperationalCategory, product.operational_category_id)
        assert (product.name, product.barcode, product.active) == (spec["name"], None, True)
        assert (product.unit_cost, product.sale_price) == (Decimal(spec["unit_cost"]), Decimal(spec["sale_price"]))
        assert category.code == spec["category_code"]
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.product_id == product.id)) == 0
    assert db.scalar(select(func.count()).select_from(CatalogSyncOperation).where(
        CatalogSyncOperation.action == "CREATE_POS_PRODUCT")) == 7
    rows, _ = statuses(db, alegra)
    assert all(rows[s["name"]]["sync_status"] == "ALREADY_LINKED" for s in sync.PRIORITY_POS_PRODUCTS)


@pytest.mark.parametrize("breaker", ["held", "inactive", "renamed", "local_name", "no_category", "missing"])
def test_reverse_any_conflict_aborts_the_whole_batch(reverse, breaker):
    db, alegra = reverse
    from app.models import OperationalCategory
    if breaker == "held":
        prods = db.scalar(select(Product).where(Product.name == "PAN")); prods.alegra_item_id = "98"; db.commit()
    elif breaker == "inactive":
        next(i for i in alegra.items if i["id"] == "46")["status"] = "inactive"
    elif breaker == "renamed":
        next(i for i in alegra.items if i["id"] == "27")["name"] = "AREPA CON SAL"
    elif breaker == "local_name":
        db.add(Product(barcode=None, name="clavos de olor", sale_price=Decimal(1), unit_cost=Decimal(1))); db.commit()
    elif breaker == "no_category":
        db.delete(db.scalar(select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))); db.commit()
    elif breaker == "missing":
        alegra.items = [i for i in alegra.items if i["id"] != "150"]
    before = db.scalar(select(func.count()).select_from(Product))
    result = sync.create_pos_products_for_alegra_items(db, sync.PRIORITY_POS_PRODUCTS, alegra, dry_run=False)
    assert result["status"] == "ABORTED" and result["problems"]
    assert db.scalar(select(func.count()).select_from(Product)) == before           # nothing partial

def test_catalog_scan_survives_transient_network_errors():
    class Flaky:
        def __init__(self): self.fails = {30: 2}
        def list_items_page(self, start=0, limit=30):
            if self.fails.get(start):
                self.fails[start] -= 1
                raise AlegraNetworkError("red")
            return [{"id": str(i), "reference": str(i)} for i in range(start, min(start + 30, 45))], 45
    assert len(fetch_all_items(Flaky(), pause=0, retry_pause=0)) == 45


def test_catalog_scan_gives_up_after_repeated_network_errors():
    class Down:
        def list_items_page(self, start=0, limit=30):
            raise AlegraNetworkError("red")
    with pytest.raises(AlegraError):
        fetch_all_items(Down(), pause=0, retry_pause=0)
