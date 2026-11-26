"""Twenty scripted evaluation probes. These verify the harness, not LLM accuracy."""

import asyncio
import json
from datetime import date
from pathlib import Path

from app.services.business_agent_conversations import AgentConversationStore
from synthetic_business_agent_data import add_uncalculable_sale, synthetic_analytics
from test_business_agent import ScriptedModel


CASES = json.loads((Path(__file__).parent / "data/business_agent_eval.json").read_text(encoding="utf-8"))


def _arguments(case: dict, name: str, index: int):
    if name == "get_purchase_agenda":
        return {"as_of": "2026-11-24"}
    if name in {"get_sales_summary", "get_profit_summary", "get_top_products",
                "get_supplier_commitments"}:
        if case["id"] in {"E14", "E15"}:
            return {"date_from": "2020-01-01", "date_to": "2020-01-01"}
        if case["id"] == "E02":
            return {"date_from": "2026-11-23", "date_to": "2026-11-29"}
        if case["id"] in {"E08", "E16"} and name == "get_supplier_commitments":
            return {"date_from": "2026-11-24", "date_to": "2026-11-30"}
        day = "2026-11-17" if index == 1 and case["id"] in {"E11", "E12", "E13"} else "2026-11-24"
        return {"date_from": day, "date_to": day}
    return {}


def test_twenty_cases_run_with_synthetic_scripted_model():
    async def scenario():
        assert len(CASES) == 20 and len({case["id"] for case in CASES}) == 20
        store = AgentConversationStore()
        results = []
        engine, db, data = synthetic_analytics()
        day = date(2026, 11, 24)
        assert data.sales_summary(day, day).net_sales == 100
        assert data.gross_profit(day, day).gross_profit == 40
        assert data.top_products(day, day).items[0].net_units == 2
        assert data.low_stock().items[0].stock == 1
        assert data.inventory_summary().registered_stock_value == 60
        assert data.supplier_obligations(day, day).estimated_total == 20
        assert data.purchase_agenda(day).total_matching == 201
        assert data.purchase_agenda(day).truncated is True
        for case in CASES:
            if case["id"] == "E15":
                add_uncalculable_sale(db)
                assert data.gross_profit(date(2020, 1, 1), date(2020, 1, 1)).state == "insufficient_data"
            calls = [(name, _arguments(case, name, index)) for index, name in enumerate(case["tools"])]
            model = ScriptedModel([calls, "Resultado sintético; consultar indicadores."] if calls
                                  else ["Ventas inventadas: 999999."])
            result = await store.run("comprefacil_synthetic", case.get("thread", case["id"]),
                                     case["question"], data, model_override=model)
            assert result.tools_used == case["tools"], case["id"]
            assert result.usage.model_calls <= 4
            if case["id"] == "E12":
                assert "Compara las ventas" in str(model.inputs[0])
            if not calls:
                assert "999999" not in result.answer
            results.append(result)
        assert len(results) == 20
        assert len(store._sessions[("comprefacil_synthetic", "comparison")].messages) == 6
        db.close()
        engine.dispose()
    asyncio.run(scenario())


def test_e09_reports_total_and_sample_when_agenda_is_truncated():
    async def scenario():
        engine, db, data = synthetic_analytics()
        try:
            case = next(item for item in CASES if item["id"] == "E09")
            model = ScriptedModel([[('get_purchase_agenda', {"as_of": "2026-11-24"})],
                                   "Hay 200 pedidos programados."])
            result = await AgentConversationStore().run(
                "synthetic", "E09", case["question"], data, model_override=model)
            assert result.tools_used == case["tools"]
            assert "201 pedidos coincidentes" in result.answer
            assert "200 representados" in result.answer
            assert "truncados" in result.answer
            assert '"shown_orders":200' in str(model.inputs[-1]).replace(" ", "")
        finally:
            db.close()
            engine.dispose()
    asyncio.run(scenario())


def test_e17_does_not_infer_payment_capacity_from_profit():
    async def scenario():
        engine, db, data = synthetic_analytics()
        try:
            case = next(item for item in CASES if item["id"] == "E17")
            calls = [(name, _arguments(case, name, index))
                     for index, name in enumerate(case["tools"])]
            model = ScriptedModel([calls, "Sí, podemos pagar con la ganancia bruta."])
            result = await AgentConversationStore().run(
                "synthetic", "E17", case["question"], data, model_override=model)
            assert result.tools_used == case["tools"]
            assert result.answer.startswith("No puedo determinar la capacidad de pago")
            assert "Ganancia bruta consultada: 40" in result.answer
            assert "Compromisos estimados: 20" in result.answer
            assert "Obligaciones reales pendientes: 0" in result.answer
            assert "dinero disponible" in result.answer
            assert "Sí, podemos pagar" not in result.answer
            rephrased = ScriptedModel([calls, "Sí, alcanza para cubrirlos."])
            second = await AgentConversationStore().run(
                "synthetic", "E17-variant",
                "¿Alcanza la ganancia bruta del 24 de noviembre de 2026 para cubrir los compromisos?",
                data, model_override=rephrased)
            assert second.answer.startswith("No puedo determinar la capacidad de pago")
            assert "Sí, alcanza" not in second.answer
        finally:
            db.close()
            engine.dispose()
    asyncio.run(scenario())


def test_e18_asks_for_period_without_model_or_tools():
    async def scenario():
        engine, db, data = synthetic_analytics()
        try:
            case = next(item for item in CASES if item["id"] == "E18")
            model = ScriptedModel([[('get_sales_summary', {
                "date_from": "2026-11-01", "date_to": "2026-11-26"})]])
            result = await AgentConversationStore().run(
                "synthetic", "E18", case["question"], data, model_override=model)
            assert result.tools_used == case["tools"] == []
            assert result.periods == [] and result.usage.model_calls == 0
            assert "período" in result.answer and model.inputs == []
        finally:
            db.close()
            engine.dispose()
    asyncio.run(scenario())
