"""Optional 20-question LLM evaluation; never run without owner authorization.

Only synthetic SQLite records and the real analytics contracts are supplied. The script
must be launched with a dedicated test API key and DATABASE_URL=sqlite://.
No call occurs at import time or without RUN_BUSINESS_AGENT_LIVE=1.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__" and os.getenv("RUN_BUSINESS_AGENT_LIVE") != "1":
    raise SystemExit("Falta autorizacion explicita para la evaluacion real")

from app.core.config import settings
from app.services.business_agent_conversations import AgentConversationStore
from synthetic_business_agent_data import add_uncalculable_sale, synthetic_analytics
from test_business_agent_eval import CASES


PRICE_INPUT_PER_MILLION = 0.75
PRICE_OUTPUT_PER_MILLION = 4.50
MAX_QUERIES = 20
MAX_ESTIMATED_USD = 0.50
MAX_QUERY_ESTIMATED_USD = 0.03


async def main() -> None:
    if os.getenv("RUN_BUSINESS_AGENT_LIVE") != "1":
        raise SystemExit("Falta autorizacion explicita para la evaluacion real")
    if os.getenv("DATABASE_URL") != "sqlite://" or os.getenv("BUSINESS_AGENT_ENABLED", "").lower() != "true":
        raise SystemExit("La evaluacion exige DATABASE_URL=sqlite:// y el feature flag de prueba")
    if (settings.business_agent_use_receipt_ai_key or not os.getenv("BUSINESS_AGENT_OPENAI_API_KEY")
            or settings.business_agent_model != "gpt-5.4-mini"):
        raise SystemExit("Se exige una key de prueba dedicada y gpt-5.4-mini")
    output = Path(os.environ.get("BUSINESS_AGENT_EVAL_OUTPUT", "")).expanduser().resolve(strict=False)
    if not output.is_absolute() or not output.parent.is_dir() or output.exists():
        raise SystemExit("BUSINESS_AGENT_EVAL_OUTPUT debe ser una ruta absoluta nueva en una carpeta existente")
    if Path(__file__).resolve().parents[3] in output.parents:
        raise SystemExit("No guardar resultados de evaluacion dentro del repositorio")

    store = AgentConversationStore()
    spent = 0.0
    stop_reason = "completed"
    engine, db, data = synthetic_analytics()
    with output.open("x", encoding="utf-8") as report:
        for case in CASES[:MAX_QUERIES]:
            if spent + MAX_QUERY_ESTIMATED_USD > MAX_ESTIMATED_USD:
                stop_reason = "budget_reserve"
                break
            if case["id"] == "E15":
                add_uncalculable_sale(db)
            try:
                result = await store.run("comprefacil_synthetic", case.get("thread", case["id"]),
                                         case["question"], data)
                usage = result.usage
                cost = (usage.input_tokens * PRICE_INPUT_PER_MILLION
                        + usage.output_tokens * PRICE_OUTPUT_PER_MILLION) / 1_000_000
                spent += cost
                if (usage.model_calls > 4 or usage.total_tokens > 8000 or usage.output_tokens > 2400
                        or cost > MAX_QUERY_ESTIMATED_USD or spent > MAX_ESTIMATED_USD):
                    stop_reason = "unexpected_consumption"
                if case["id"] == "E01" and (result.tools_used != ["get_sales_summary"]
                                                or usage.model_calls == 0):
                    stop_reason = "model_or_tool_unavailable"
                record = {"id": case["id"], "model": settings.business_agent_model,
                          "expected_tools": case["tools"], "tools_used": result.tools_used,
                          "tool_match": sorted(case["tools"]) == sorted(result.tools_used),
                          "expected_facts": case["facts"], "answer": result.answer,
                          "periods": [period.model_dump(mode="json") for period in result.periods],
                          "limitations": result.limitations, "usage": usage.model_dump(),
                          "latency_ms": result.duration_ms, "estimated_usd": round(cost, 6),
                          "indicator_accuracy": "manual_review_required"}
            except Exception as exc:
                record = {"id": case["id"], "model": settings.business_agent_model,
                          "error_type": type(exc).__name__, "indicator_accuracy": "not_evaluated"}
                stop_reason = "provider_or_limit_error"
            report.write(json.dumps(record, ensure_ascii=False) + "\n")
            report.flush()
            if case["id"] == "E01":
                print({"availability_probe": "pass" if stop_reason == "completed" else "stop",
                       "model": settings.business_agent_model, "tokens": record.get("usage", {}).get("total_tokens", 0),
                       "estimated_usd": record.get("estimated_usd", 0)})
            if stop_reason != "completed":
                break  # Cost and token accounting are post-run, never a hard provider spend cap.
    db.close()
    engine.dispose()
    print({"completed_cases": sum(1 for _ in output.open(encoding="utf-8")),
           "estimated_usd": round(spent, 6), "stop_reason": stop_reason, "report": str(output)})


if __name__ == "__main__":
    asyncio.run(main())
