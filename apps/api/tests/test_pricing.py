from decimal import Decimal

import pytest

from app.services.pricing import (commercial_round, gross_margin_percent,
                                  mathematical_sale_price, suggested_sale_price)


def test_gross_margin_uses_sale_price_as_denominator():
    assert gross_margin_percent(Decimal("18500"), Decimal("13000")) == Decimal("29.73")


def test_mathematical_price_for_thirty_percent_margin():
    result = mathematical_sale_price(Decimal("13000"), Decimal("30"))
    assert result == Decimal("18571.42857142857142857142857")


@pytest.mark.parametrize("margin", [Decimal("100"), Decimal("-1"), Decimal("0")])
def test_invalid_target_margin_is_rejected(margin):
    with pytest.raises(ValueError):
        mathematical_sale_price(Decimal("13000"), margin)


@pytest.mark.parametrize(("amount", "expected"), [
    ("18571", "19000"), ("18020", "18500"), ("18500", "18500")])
def test_round_up_500_policy(amount, expected):
    assert commercial_round(Decimal(amount), "round_up_500") == Decimal(expected)


def test_suggested_price_combines_margin_and_commercial_rounding():
    calculated, suggested = suggested_sale_price(
        Decimal("13000"), Decimal("30"), "round_up_500")
    assert calculated > Decimal("18571")
    assert suggested == Decimal("19000")
