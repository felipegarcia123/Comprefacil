"""Synthetic-only FastAPI server for the fully internal browser chain."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.getenv("COMP_S2_004_CHAIN_TEST") != "1":
    raise SystemExit("Synthetic chain test flag required")
if "@comps2004-db:5432/comp_s2_004" not in os.getenv("BUSINESS_AGENT_DATABASE_URL", ""):
    raise SystemExit("Refusing a non-experimental PostgreSQL destination")

import app.services.business_agent_conversations as module
from app.services.business_agent import run_business_agent
from test_business_agent import ScriptedModel, period_args


async def scripted_run(question, analytics, *, history=None, model_override=None):
    if "seguimiento" in question.lower():
        answer = "Seguimiento del período anterior con datos sintéticos." if history else "Contexto no disponible."
    else:
        answer = "Venta sintética verificada: 400 pesos."
    model = ScriptedModel([[('get_sales_summary', period_args())], answer])
    return await run_business_agent(question, analytics, model_override=model, history=history)


module.run_business_agent = scripted_run

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="warning")
