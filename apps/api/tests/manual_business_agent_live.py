"""Optional real-model smoke test. Never run without explicit authorization.

Required: RUN_BUSINESS_AGENT_LIVE=1, DATABASE_URL=sqlite://,
BUSINESS_AGENT_ENABLED=true, BUSINESS_AGENT_OPENAI_API_KEY in the environment.
No commercial records are loaded. No provider call occurs on import.
"""

import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo


async def main() -> None:
    if os.getenv("RUN_BUSINESS_AGENT_LIVE") != "1":
        raise SystemExit("Prueba real no autorizada")
    if os.getenv("DATABASE_URL") != "sqlite://":
        raise SystemExit("La prueba real requiere DATABASE_URL=sqlite://")
    if os.getenv("BUSINESS_AGENT_ENABLED", "").lower() != "true":
        raise SystemExit("El feature flag debe habilitarse explicitamente")
    if not os.getenv("BUSINESS_AGENT_OPENAI_API_KEY"):
        raise SystemExit("Falta una API key exclusiva para la prueba")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.db import Base
    from app.services.business_agent import run_business_agent
    from app.services.business_analytics import PosBusinessAnalytics

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        day = datetime.now(ZoneInfo("America/Bogota")).date().isoformat()
        result = await run_business_agent(
            f"Cuanto se vendio el {day}? Consulta los datos antes de responder.",
            PosBusinessAnalytics(db),
        )
        assert result.tools_used == ["get_sales_summary"]
        assert result.usage.model_calls <= 4
        print({"tools_used": result.tools_used, "usage": result.usage.model_dump(),
               "answer": result.answer})
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
