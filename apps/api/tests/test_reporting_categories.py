from app.services.reporting_categories import BULTOS, reporting_category


def test_only_explicit_bulto_markers_split_the_combined_stored_category():
    assert reporting_category("MASCOTAS_SIN_CODIGO", manual_kind="BULTO") == BULTOS
    assert reporting_category("MASCOTAS_SIN_CODIGO", sale_source="MANUAL_SACK") == BULTOS
    assert reporting_category("MASCOTAS_SIN_CODIGO", manual_kind="GRANEL") == "MASCOTAS_SIN_CODIGO"
    assert reporting_category("MASCOTAS_SIN_CODIGO", manual_kind="PET_OTHER") == "MASCOTAS_SIN_CODIGO"


def test_ambiguous_historical_catalog_lines_remain_in_their_stored_group():
    # There is no reliable bulto marker on this historical catalog line; do not guess.
    assert reporting_category("MASCOTAS_SIN_CODIGO") == "MASCOTAS_SIN_CODIGO"
