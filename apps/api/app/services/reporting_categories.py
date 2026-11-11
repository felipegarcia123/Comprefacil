"""Read-only operational categories used by summaries and reports.

``BULTOS`` is deliberately a reporting code, not a stored operational
category.  Before manual lines got ``manual_kind`` (migration 0025), a sack
sale was identified by ``Sale.source == MANUAL_SACK``.  The historical rows
must remain immutable, so reports derive the split at read time instead of
rewriting their ``category_code``.

Catalog lines already stored as ``MASCOTAS_SIN_CODIGO`` without either marker
remain in that group.  In the current production history there are seven such
lines ($102,000); their source data cannot reliably say whether they were a
sack or an unbarcoded pet item, so this module never guesses.
"""

from __future__ import annotations


MASCOTAS_SIN_CODIGO = "MASCOTAS_SIN_CODIGO"
BULTOS = "BULTOS"
MASCOTAS_SIN_CODIGO_NAME = "Mascotas sin código"
BULTOS_NAME = "Bultos"


def reporting_category(code: str | None, *, manual_kind: str | None = None,
                       sale_source: object | None = None) -> str:
    """Return the display/reporting category without changing persisted data."""
    code = code or "SIN_CLASIFICAR"
    source = getattr(sale_source, "value", sale_source)
    if code == MASCOTAS_SIN_CODIGO and (manual_kind == "BULTO" or source == "MANUAL_SACK"):
        return BULTOS
    return code


def reporting_category_name(code: str, stored_names: dict[str, str]) -> str:
    """Return independent presentation labels without changing the stored category."""
    if code == MASCOTAS_SIN_CODIGO:
        return MASCOTAS_SIN_CODIGO_NAME
    return BULTOS_NAME if code == BULTOS else stored_names.get(code, code)


def reporting_category_order(code: str, stored_orders: dict[str, int]) -> int:
    """Keep Bultos directly after Mascotas sin código in every read model."""
    if code == BULTOS:
        return stored_orders.get(MASCOTAS_SIN_CODIGO, 30) + 1
    return stored_orders.get(code, 999)
