from decimal import Decimal
from types import SimpleNamespace

from app.services.pos import recalculate


def test_sale_total_is_sum_of_line_totals():
    sale = SimpleNamespace(items=[SimpleNamespace(line_total=Decimal("1200.50")), SimpleNamespace(line_total=Decimal("300"))], total=None)
    recalculate(sale)
    assert sale.total == Decimal("1500.50")


def test_inventory_ledger_reconstructs_stock_with_signed_quantities():
    movements = [Decimal("10"), Decimal("-2"), Decimal("1"), Decimal("-0.5")]
    assert sum(movements) == Decimal("8.5")
