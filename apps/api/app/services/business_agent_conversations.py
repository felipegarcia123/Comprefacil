"""Process-local, short-lived conversation context with no tool payload retention."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from time import monotonic

from app.schemas.business_agent import BusinessAgentResponse
from app.services.business_agent import run_business_agent
from app.services.business_analytics import BusinessAnalyticsSource


TTL_SECONDS = 30 * 60
MAX_SESSIONS = 128
MAX_MESSAGES = 10
MAX_HISTORY_CHARS = 3000  # Conservative bound; model run also has a token limit.


@dataclass
class Conversation:
    expires_at: float
    messages: list[dict[str, str]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class AgentConversationStore:
    def __init__(self):
        self._sessions: OrderedDict[tuple[str, str], Conversation] = OrderedDict()

    def _entry(self, business_id: str, session_id: str) -> Conversation:
        now = monotonic()
        for key, entry in list(self._sessions.items()):
            if entry.expires_at <= now and not entry.lock.locked():
                self._sessions.pop(key, None)
        key = (business_id, session_id)
        entry = self._sessions.get(key)
        if entry is None:
            while len(self._sessions) >= MAX_SESSIONS:
                # Never evict an in-flight turn; refuse capacity instead.
                idle = next((k for k, v in self._sessions.items() if not v.lock.locked()), None)
                if idle is None:
                    raise RuntimeError("Limite de conversaciones alcanzado")
                self._sessions.pop(idle)
            entry = Conversation(expires_at=now + TTL_SECONDS)
            self._sessions[key] = entry
        self._sessions.move_to_end(key)
        return entry

    async def run(self, business_id: str, session_id: str, question: str,
                  analytics: BusinessAnalyticsSource, *, model_override=None) -> BusinessAgentResponse:
        entry = self._entry(business_id, session_id)
        async with entry.lock:
            if entry.expires_at <= monotonic():
                entry.messages.clear()
            history = [item.copy() for item in entry.messages]
            result = await run_business_agent(question, analytics, model_override=model_override,
                                              history=history)
            if result.tools_used:
                period_hint = "; ".join(f"{p.date_from} a {p.date_to}" for p in result.periods[:4])
                remembered_answer = result.answer[:1100]
                if period_hint:
                    remembered_answer += f"\nPeriodos consultados: {period_hint}"
                entry.messages.extend(({"role": "user", "content": question[:600]},
                                       {"role": "assistant", "content": remembered_answer[:1200]}))
                entry.messages = entry.messages[-MAX_MESSAGES:]
                while sum(len(item["content"]) for item in entry.messages) > MAX_HISTORY_CHARS:
                    del entry.messages[:2]
            entry.expires_at = monotonic() + TTL_SECONDS
            return result


conversations = AgentConversationStore()
