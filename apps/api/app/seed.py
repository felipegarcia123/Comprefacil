from decimal import Decimal
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domain.enums import InventoryMovementType
from app.models import InventoryMovement, Product, Supplier, User


def seed():
    with SessionLocal() as db:
        # 0028: business accounts (Nequi / Davivienda / Nu), idempotent on every start.
        from app.services.business_accounts import ensure_default_accounts
        ensure_default_accounts(db); db.commit()
        if db.scalar(select(User).limit(1)):
            return
        user = User(name="Administrador")
        supplier = Supplier(name="Distribuidor Demo")
        db.add_all([user, supplier]); db.flush()
        rows = [
            ("7702004001234", "Café colombiano 500 g", "18500", "13000", "30"),
            ("7702011000015", "Leche entera 1 L", "4800", "3500", "50"),
            ("7702020000451", "Pan tajado", "7200", "4900", "25"),
            ("7702030000786", "Chocolate de mesa", "9500", "6800", "20"),
            ("7702040000990", "Agua 600 ml", "2500", "1200", "80"),
        ]
        for barcode, name, price, cost, qty in rows:
            product = Product(barcode=barcode, name=name, sale_price=Decimal(price),
                              unit_cost=Decimal(cost), supplier_id=supplier.id)
            db.add(product); db.flush()
            db.add(InventoryMovement(product_id=product.id,
                   movement_type=InventoryMovementType.PURCHASE, quantity=Decimal(qty),
                   unit_cost=Decimal(cost), reference_id=None, user_id=user.id))
        db.commit()


if __name__ == "__main__":
    seed()

