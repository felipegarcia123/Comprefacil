"""Channel-independent contract for an experimental business conversation."""

from datetime import date

from pydantic import BaseModel, Field


class BusinessAgentRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600)


class AnalyzedPeriod(BaseModel):
    date_from: date
    date_to: date
    timezone: str = "America/Bogota"


class AgentTokenUsage(BaseModel):
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class BusinessAgentResponse(BaseModel):
    answer: str
    tools_used: list[str]
    periods: list[AnalyzedPeriod]
    limitations: list[str]
    usage: AgentTokenUsage
    duration_ms: int
