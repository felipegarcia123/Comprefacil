# Experimental business agent (COMP-S2-002 / COMP-S2-004)

The single-agent `POST /api/v1/agent/chat` endpoint uses the seven
read-only contracts from COMP-S2-001. It has no write tools, arbitrary SQL,
RAG, web search, handoffs, or external business integrations. Web, mobile and
WhatsApp can later call the same server service after proper channel auth.
Without `X-Conversation-Session` it remains stateless. The web bridge derives
that opaque header from a signed, random, HTTP-only cookie; the browser sends
only the question. An authenticated channel can opt into process-local context
using its own server-generated 64-character hex session ID. Business scope is
set by the server, never by a model or browser parameter.

## Dependencies and model

`openai-agents==0.21.1` and `starlette==0.41.3` are pinned alongside FastAPI
`0.115.6`. Newer SDK releases tested here required Starlette outside that
FastAPI version's supported range. The candidate model is `gpt-5.4-mini`,
configurable through `BUSINESS_AGENT_MODEL`; its API model page documents
Responses and function calling support:
https://developers.openai.com/api/docs/models/gpt-5.4-mini

Availability for this account was verified with the dedicated test key on
November 25, 2026. The 20-case real-model evaluation found three material
answer failures; see `docs/business-agent-evaluation.md`. This does not
authorize operational access.

## Configuration

Everything is off by default. For an **isolated, authorized** environment set:

| Variable | Purpose |
| --- | --- |
| `BUSINESS_AGENT_ENABLED=true` | Enables the route. Otherwise it returns 404. |
| `BUSINESS_AGENT_ACCESS_TOKEN` | Separate random bearer token, at least 32 characters. Never commit it. |
| `BUSINESS_AGENT_DATABASE_URL` | PostgreSQL URL for a distinct role with SELECT only. No fallback to `DATABASE_URL`. |
| `BUSINESS_AGENT_OPENAI_API_KEY` | Dedicated OpenAI API key. Never commit it. |
| `BUSINESS_AGENT_MODEL` | Defaults to `gpt-5.4-mini`. |
| `BUSINESS_AGENT_USE_RECEIPT_AI_KEY=true` | Optional explicit fallback to the existing receipt AI key. Default false. |

The URL must use `postgresql+psycopg`. The endpoint rejects a role with
superuser, database/schema CREATE, table write, or sequence UPDATE privileges.
Each request uses `REPEATABLE READ READ ONLY` and a 5-second SQL statement
timeout. Provision the role and grants separately; this ticket does not alter
the operational database. The endpoint uses a server-owned Comprefácil source
and accepts no client/model-supplied business ID or permission scope.

Example request shape, with placeholders only:

```http
POST /api/v1/agent/chat
Authorization: Bearer <token-from-secret-store>
Content-Type: application/json

{"question":"¿Cuánto se vendió el 24 de noviembre de 2026?"}
```

The response includes `answer`, `tools_used`, `periods`, `limitations`,
`usage` (model calls and tokens), and `duration_ms`. Provider failures return
generic errors without keys or prompts. At most five previous exchanges are
kept in process memory for 30 minutes after the last turn. Only the user text,
final answer and analyzed dates are remembered, never raw tool results. The
history is limited to 3,000 characters and the store to 128 sessions. Runs in
one session are serialized. A process restart or multi-worker routing can lose
context; no persistent memory or shared store is configured. New conversation
rotates the cookie, so the old context cannot be reused by that browser.

## Privacy and limits

SDK tracing and sensitive trace payloads are disabled; Responses storage is
set to `false`. The model receives only short aggregate tool results, at most
10 products or 10 grouped agenda days. Purchase/customer IDs, fiscal data,
supplier names and documents are omitted. Basic input screening rejects
credentials, email addresses, fiscal IDs and long numeric identifiers. It is
not a substitute for full data-loss prevention; do not submit personal or
fiscal information to this experimental route.

The run is bounded to at most 4 model turns, 7 tools, 600 output tokens per
model call, 8,000 total tokens checked after the run, and 25 seconds total.
There is no retry of model requests. The total-token check is an output gate,
not a pre-charge guarantee; use account-level quotas before broader access.
History increases input tokens. A follow-up must requery tools before asserting
commercial figures, including both periods in a comparison.
No public exposure is approved. The bearer token is an experimental gate,
not a replacement for per-user authorization or a distributed rate limiter.

## Verification

`tests/test_business_agent.py` uses a scripted Agents SDK model and synthetic
analytics; it makes no OpenAI call. An isolated PostgreSQL test confirmed that
a SELECT-only role can run the agenda and a write-capable role is rejected.

`tests/manual_business_agent_live.py` is an **optional, unexecuted** smoke test
using an empty SQLite database. It requires an explicit authorization, the
`RUN_BUSINESS_AGENT_LIVE=1` gate, `DATABASE_URL=sqlite://`, the feature flag,
and a dedicated API key. It checks actual model availability and selection of
`get_sales_summary` without sending store records.

COMP-S2-004 adds 20 evaluation cases in `tests/data/business_agent_eval.json`,
scripted checks in `tests/test_business_agent_eval.py`, and an optional guarded
real-model runner in `tests/manual_business_agent_eval.py`. The owner
authorized one run of the 20 cases on November 25, 2026. It used only
synthetic analytics and recorded model, token use, estimated cost, latency,
tools, periods, answer, limitations and expected facts. The model was
available, but the first acceptance review failed on E09, E17 and E18. Focused
corrections and regression tests were followed by one authorized real-model
reevaluation of the same 20 synthetic cases: 20/20 tool selections and 20/20
acceptable answers, with no provider or limit errors. See
`docs/business-agent-evaluation.md` for the comparison and limitations. Do not
repeat real-model calls without a new authorization.
