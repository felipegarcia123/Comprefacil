from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Product
from app.schemas.products import (AlegraMappingIn, ProductActiveIn, ProductCatalogOut,
                                  ProductCreate, ProductUpdate)
from app.schemas.product_imports import ProductImportPreview, ProductImportRequest, ProductImportResult
from app.services.barcodes import find_product_by_barcode
from app.services.inventory import inventory_products_query
from app.services.product_imports import confirm_import, validate_import
from app.services.products import create_product, set_alegra_mapping, set_product_active, update_product

router = APIRouter(prefix="/products", tags=["products"])


def product_out(db: Session, product_id: UUID) -> ProductCatalogOut:
    row = db.execute(inventory_products_query().where(Product.id == product_id)).first()
    if not row:
        raise HTTPException(404, "Producto no encontrado")
    product, supplier, category, stock, last_cost = row
    return ProductCatalogOut(
        id=product.id, barcode=product.barcode, name=product.name,
        sale_price=product.sale_price, purchase_cost=product.unit_cost,
        category_id=product.operational_category_id, category=category,
        supplier_id=product.supplier_id, supplier=supplier,
        minimum_stock=product.minimum_stock, unit=product.unit,
        active=product.active, description=product.description,
        current_stock=stock, last_purchase_cost=last_cost,
        alegra_item_id=product.alegra_item_id, alegra_tax_id=product.alegra_tax_id,
        created_at=product.created_at, updated_at=product.updated_at)


@router.post("", response_model=ProductCatalogOut, status_code=201)
def create(body: ProductCreate, db: Session = Depends(get_db)):
    product = create_product(db, body)
    return product_out(db, product.id)


@router.post("/import/preview", response_model=ProductImportPreview)
def import_preview(body: ProductImportRequest, db: Session = Depends(get_db)):
    return validate_import(db, body)


@router.post("/import/confirm", response_model=ProductImportResult)
def import_confirm(body: ProductImportRequest, db: Session = Depends(get_db)):
    return confirm_import(db, body)


@router.get("/import/template")
def import_template():
    content = ("barcode,name,category,supplier,purchase_cost,sale_price,initial_stock,"
               "minimum_stock,unit,description,active\n")
    return Response("\ufeff" + content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=plantilla-productos.csv"})


@router.get("/by-barcode", response_model=ProductCatalogOut)
def by_barcode(barcode: str = Query(min_length=1, max_length=64), db: Session = Depends(get_db)):
    product = find_product_by_barcode(db, barcode)
    if not product:
        raise HTTPException(404, "No existe un producto con este código")
    return product_out(db, product.id)


@router.get("/{product_id}", response_model=ProductCatalogOut)
def detail(product_id: UUID, db: Session = Depends(get_db)):
    return product_out(db, product_id)


@router.patch("/{product_id}", response_model=ProductCatalogOut)
def update(product_id: UUID, body: ProductUpdate, db: Session = Depends(get_db)):
    product = update_product(db, product_id, body)
    return product_out(db, product.id)


@router.patch("/{product_id}/active", response_model=ProductCatalogOut)
def activate(product_id: UUID, body: ProductActiveIn, db: Session = Depends(get_db)):
    product = set_product_active(db, product_id, body.active)
    return product_out(db, product.id)


@router.patch("/{product_id}/alegra-mapping", response_model=ProductCatalogOut)
def alegra_mapping(product_id: UUID, body: AlegraMappingIn, db: Session = Depends(get_db)):
    product = set_alegra_mapping(db, product_id, body.alegra_item_id, body.alegra_tax_id)
    return product_out(db, product.id)
