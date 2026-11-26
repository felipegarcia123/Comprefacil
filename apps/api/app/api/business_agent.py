"""Experimental, authenticated channel-independent conversation endpoint."""

from hmac import compare_digest
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.business_agent import BusinessAgentRequest, BusinessAgentResponse
from app.services.business_agent import (AgentLimitError, AgentProviderError,
                                         run_business_agent)
from app.services.business_agent_conversations import conversations
from app.services.business_analytics import BusinessAnalyticsSource, PosBusinessAnalytics


router = APIRouter(prefix="/agent", tags=["experimental-business-agent"])


def require_agent_auth(authorization: str | None = Header(default=None)) -> None:
    if not settings.business_agent_enabled:
        raise HTTPException(404, "No disponible")
    token = settings.business_agent_access_token
    if len(token) < 32:
        raise HTTPException(503, "Agente no configurado")
    candidate = authorization.removeprefix("Bearer ") if authorization else ""
    if not authorization or not authorization.startswith("Bearer ") or not compare_digest(candidate, token):
        raise HTTPException(401, "No autorizado", headers={"WWW-Authenticate": "Bearer"})


def readonly_analytics_source():
    """Require a distinct PostgreSQL role with no write grants, plus read-only transaction."""
    raw_url = settings.business_agent_database_url
    if not raw_url or raw_url == settings.database_url:
        raise HTTPException(503, "Base de lectura no configurada")
    try:
        if make_url(raw_url).drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL requerido")
        engine = create_engine(raw_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    except Exception as exc:
        raise HTTPException(503, "Base de lectura no configurada") from exc
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            privileged = connection.scalar(text("""
                SELECT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
                    OR has_database_privilege(current_user, current_database(), 'CREATE')
                    OR has_schema_privilege(current_user, 'public', 'CREATE')
                    OR EXISTS (
                        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                          AND ((c.relkind IN ('r', 'p') AND (
                                  has_table_privilege(current_user, c.oid, 'INSERT')
                               OR has_table_privilege(current_user, c.oid, 'UPDATE')
                               OR has_table_privilege(current_user, c.oid, 'DELETE')
                               OR has_table_privilege(current_user, c.oid, 'TRUNCATE')))
                            OR (c.relkind = 'S' AND has_sequence_privilege(current_user, c.oid, 'UPDATE')))
                    )
            """))
            if privileged:
                raise HTTPException(503, "El rol analitico requiere permisos de solo lectura")
            try:
                with Session(bind=connection, autoflush=False) as db:
                    yield PosBusinessAnalytics(db)
            finally:
                transaction.rollback()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Base de lectura no disponible") from exc
    finally:
        engine.dispose()


def get_agent_source(_: None = Depends(require_agent_auth)):
    yield from readonly_analytics_source()


@router.post("/chat", response_model=BusinessAgentResponse)
async def chat(body: BusinessAgentRequest,
               _: None = Depends(require_agent_auth),
               analytics: BusinessAnalyticsSource = Depends(get_agent_source),
               x_conversation_session: str | None = Header(default=None)):
    if not settings.business_agent_openai_api_key and not (
            settings.business_agent_use_receipt_ai_key and settings.receipt_ai_openai_api_key):
        raise HTTPException(503, "Modelo no configurado")
    try:
        if x_conversation_session is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", x_conversation_session):
                raise HTTPException(400, "Sesion de conversacion invalida")
            return await conversations.run("comprefacil_pos", x_conversation_session,
                                           body.question.strip(), analytics)
        return await run_business_agent(body.question.strip(), analytics)
    except AgentLimitError as exc:
        raise HTTPException(429, "La consulta excedio los limites del agente") from exc
    except AgentProviderError as exc:
        raise HTTPException(502, "El proveedor de IA no esta disponible") from exc
    except RuntimeError as exc:
        raise HTTPException(429, "Demasiadas conversaciones activas") from exc
