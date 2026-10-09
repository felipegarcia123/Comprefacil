from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models import Product
from app.schemas.pricing import AppliedPriceOut, ApplySuggestedPriceIn, PricingConfigOut
from app.services.pricing import (RoundingPolicy, ROUNDING_STEPS, gross_margin_percent,
                                  suggested_sale_price)

router = APIRouter(tags=["pricing"])


def configured_policy() -> RoundingPolicy:
    if settings.commercial_rounding_policy not in ROUNDING_STEPS:
        raise HTTPException(500, "La política de redondeo comercial no es válida")
    return cast(RoundingPolicy, settings.commercial_rounding_policy)


@router.get("/pricing/config", response_model=PricingConfigOut)
def pricing_config():
    return PricingConfigOut(commercial_rounding_policy=configured_policy())


@router.post("/products/{product_id}/sale-price/suggested", response_model=AppliedPriceOut)
def apply_suggested_price(product_id: UUID, body: ApplySuggestedPriceIn,
                          db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    policy = configured_policy()
    calculated, suggested = suggested_sale_price(
        body.unit_cost, body.target_margin_percent, policy)
    previous = product.sale_price
    product.sale_price = suggested
    db.commit(); db.refresh(product)
    return AppliedPriceOut(
        product_id=product.id, previous_sale_price=previous,
        sale_price=product.sale_price, mathematical_price=calculated,
        target_margin_percent=body.target_margin_percent,
        resulting_margin_percent=gross_margin_percent(product.sale_price, body.unit_cost),
        rounding_policy=policy)
