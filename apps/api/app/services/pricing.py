from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Literal


RoundingPolicy = Literal["nearest_100", "nearest_500", "round_up_100", "round_up_500"]
ROUNDING_STEPS: dict[RoundingPolicy, Decimal] = {
    "nearest_100": Decimal("100"),
    "nearest_500": Decimal("500"),
    "round_up_100": Decimal("100"),
    "round_up_500": Decimal("500"),
}


def validate_target_margin(target_margin_percent: Decimal) -> None:
    if not target_margin_percent.is_finite() or not Decimal("0") < target_margin_percent < Decimal("100"):
        raise ValueError("El margen objetivo debe ser mayor que 0 y menor que 100")


def gross_margin_percent(sale_price: Decimal, unit_cost: Decimal) -> Decimal | None:
    if sale_price <= 0:
        return None
    return ((sale_price - unit_cost) / sale_price * 100).quantize(Decimal("0.01"))


def mathematical_sale_price(unit_cost: Decimal, target_margin_percent: Decimal) -> Decimal:
    validate_target_margin(target_margin_percent)
    if not unit_cost.is_finite() or unit_cost < 0:
        raise ValueError("El costo unitario debe ser un número mayor o igual a cero")
    return unit_cost / (Decimal("1") - target_margin_percent / Decimal("100"))


def commercial_round(amount: Decimal, policy: RoundingPolicy = "round_up_500") -> Decimal:
    if policy not in ROUNDING_STEPS:
        raise ValueError("Política de redondeo no soportada")
    step = ROUNDING_STEPS[policy]
    rounding = ROUND_CEILING if policy.startswith("round_up") else ROUND_HALF_UP
    return (amount / step).quantize(Decimal("1"), rounding=rounding) * step


def suggested_sale_price(unit_cost: Decimal, target_margin_percent: Decimal,
                         policy: RoundingPolicy = "round_up_500") -> tuple[Decimal, Decimal]:
    calculated = mathematical_sale_price(unit_cost, target_margin_percent)
    return calculated, commercial_round(calculated, policy)
