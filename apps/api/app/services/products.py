from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import InventoryMovementType
from app.models import InventoryMovement, OperationalCategory, Product, Supplier
from app.schemas.products import ProductCreate, ProductUpdate
from app.services.pos import get_operator
from app.services.operational_session import require_open_session


def _relations(db: Session, category_id: UUID | None,
               supplier_id: UUID | None) -> tuple[OperationalCategory | None, Supplier | None]:
    category = db.get(OperationalCategory, category_id) if category_id else None
    if category_id and (not category or not category.active):
        raise HTTPException(404, "Categoría operativa no encontrada")
    supplier = db.get(Supplier, supplier_id) if supplier_id else None
    if supplier_id and not supplier:
        raise HTTPException(404, "Proveedor no encontrado")
    return category, supplier


def _unique_barcode(db: Session, barcode: str | None, exclude: UUID | None = None) -> None:
    if barcode is None:
        return  # many products may have no code; uniqueness applies to real codes only
    stmt = select(Product.id).where(func.lower(Product.barcode) == barcode.lower())
    if exclude:
        stmt = stmt.where(Product.id != exclude)
    if db.scalar(stmt):
        raise HTTPException(409, "Ya existe un producto con este código.")


def _apply(product: Product, body: ProductCreate | ProductUpdate) -> None:
    product.barcode = body.barcode
    product.name = body.name
    product.sale_price = body.sale_price
    product.unit_cost = body.purchase_cost
    product.operational_category_id = body.category_id
    product.supplier_id = body.supplier_id
    product.minimum_stock = body.minimum_stock
    product.unit = body.unit
    product.description = body.description
    product.active = body.active


def add_product_to_session(db: Session, body: ProductCreate, operator_id: UUID) -> Product:
    """Shared Product + INITIAL_STOCK construction used by manual and CSV creation."""
    product = Product()
    _apply(product, body)
    db.add(product)
    db.flush()
    if body.initial_stock:
        db.add(InventoryMovement(
            product_id=product.id, movement_type=InventoryMovementType.INITIAL_STOCK,
            quantity=Decimal(body.initial_stock), unit_cost=product.unit_cost,
            reference_id=None, reason="Stock inicial al crear producto",
            user_id=operator_id))
    return product


def create_product(db: Session, body: ProductCreate) -> Product:
    if body.initial_stock > 0:
        require_open_session(db, lock=True)
    """Canonical product creation entry point, reusable by a future importer."""
    try:
        _unique_barcode(db, body.barcode)
        _relations(db, body.category_id, body.supplier_id)
        product = add_product_to_session(db, body, get_operator(db).id)
        db.commit(); db.refresh(product)
        return product
    except IntegrityError as error:
        db.rollback()
        if "barcode" in str(error).lower():
            raise HTTPException(409, "Ya existe un producto con este código.")
        raise
    except Exception:
        db.rollback(); raise


def update_product(db: Session, product_id: UUID, body: ProductUpdate) -> Product:
    try:
        product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
        if not product:
            raise HTTPException(404, "Producto no encontrado")
        _unique_barcode(db, body.barcode, product.id)
        _relations(db, body.category_id, body.supplier_id)
        _apply(product, body)
        db.commit(); db.refresh(product)
        return product
    except IntegrityError as error:
        db.rollback()
        if "barcode" in str(error).lower():
            raise HTTPException(409, "Ya existe un producto con este código.")
        raise
    except Exception:
        db.rollback(); raise


def set_product_active(db: Session, product_id: UUID, active: bool) -> Product:
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    product.active = active
    db.commit(); db.refresh(product)
    return product


def set_alegra_mapping(db: Session, product_id: UUID, alegra_item_id: str | None,
                       alegra_tax_id: str | None) -> Product:
    """Explicit, one-product-at-a-time mapping — never bulk-synced."""
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    product.alegra_item_id = alegra_item_id
    product.alegra_tax_id = alegra_tax_id
    db.commit(); db.refresh(product)
    return product
