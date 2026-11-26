import { NextRequest, NextResponse } from "next/server";
import { AI_COOKIE, aiActorForSession, aiConfig, noStoreHeaders, sameOrigin } from "@/lib/businessAiServer";

export const runtime = "nodejs";

function authorized(request: NextRequest) {
  const config = aiConfig();
  const actor = config.enabled ? aiActorForSession(request.cookies.get(AI_COOKIE)?.value, config) : null;
  return { config, actor };
}

async function upstream(url: string, token: string, method: "GET" | "POST", body?: object) {
  return fetch(url, { method, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(12000),
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}

function failure(status: number) {
  const message = status === 403 ? "La confirmación venció. Revisa y confirma de nuevo."
    : status === 404 ? "El recordatorio pendiente ya no está disponible."
    : status === 409 ? "El recordatorio cambió. Actualiza el centro e intenta de nuevo."
    : status === 422 ? "Revisa el texto y la fecha del recordatorio."
    : "No se pudo consultar el centro de notificaciones.";
  return NextResponse.json({ error: message }, { status: [403, 404, 409, 422].includes(status) ? status : 502,
    headers: noStoreHeaders });
}

export async function GET(request: NextRequest) {
  const { config, actor } = authorized(request);
  if (!config.enabled || !config.notificationsEnabled) return NextResponse.json({ error: "No disponible." }, { status: 404, headers: noStoreHeaders });
  if (!actor) return NextResponse.json({ error: "Inicia sesión con tu código personal." }, { status: 401, headers: noStoreHeaders });
  try {
    const response = await upstream(`${config.notificationsUrl}/center`, actor.api_token, "GET");
    if (!response.ok) return failure(response.status);
    const data: unknown = await response.json();
    if (!data || typeof data !== "object" || !Array.isArray((data as { alerts?: unknown }).alerts)
      || !Array.isArray((data as { reminders?: unknown }).reminders)) throw new Error("Invalid center");
    return NextResponse.json(data, { headers: noStoreHeaders });
  } catch {
    return failure(502);
  }
}

export async function POST(request: NextRequest) {
  const { config, actor } = authorized(request);
  if (!config.enabled || !config.notificationsEnabled) return NextResponse.json({ error: "No disponible." }, { status: 404, headers: noStoreHeaders });
  if (!sameOrigin(request)) return NextResponse.json({ error: "Solicitud no permitida." }, { status: 403, headers: noStoreHeaders });
  if (!actor) return NextResponse.json({ error: "Inicia sesión con tu código personal." }, { status: 401, headers: noStoreHeaders });
  const raw = await request.text();
  if (raw.length > 2500) return failure(422);
  let body: Record<string, unknown>;
  try { body = JSON.parse(raw) as Record<string, unknown>; }
  catch { return failure(422); }
  if (!body || typeof body !== "object") return failure(422);
  const uuid = (value: unknown) => typeof value === "string" && /^[a-fA-F0-9-]{36}$/.test(value);
  const draft = body.draft;
  const validDraft = draft && typeof draft === "object" && typeof (draft as Record<string, unknown>).title === "string"
    && ((draft as Record<string, unknown>).title as string).trim().length >= 3
    && ((draft as Record<string, unknown>).title as string).length <= 240
    && typeof (draft as Record<string, unknown>).due_local === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test((draft as Record<string, unknown>).due_local as string)
    && uuid((draft as Record<string, unknown>).idempotency_key);
  let path = "";
  let payload: object;
  if (body.operation === "intent" && body.action === "create" && validDraft) {
    path = "/intent"; payload = { action: "create", draft };
  } else if (body.operation === "intent" && ["complete", "cancel"].includes(String(body.action))
    && uuid(body.reminder_id)) {
    path = "/intent"; payload = { action: body.action, reminder_id: body.reminder_id };
  } else if (body.operation === "create" && validDraft && typeof body.approval_token === "string") {
    path = "/reminders"; payload = { ...draft as object, approval_token: body.approval_token };
  } else if (body.operation === "resolve" && ["complete", "cancel"].includes(String(body.action))
    && uuid(body.reminder_id) && typeof body.approval_token === "string") {
    path = `/reminders/${body.reminder_id}/${body.action}`; payload = { approval_token: body.approval_token };
  } else return failure(422);
  try {
    const response = await upstream(`${config.notificationsUrl}${path}`, actor.api_token, "POST", payload);
    if (!response.ok) return failure(response.status);
    const data: unknown = await response.json();
    return NextResponse.json(data, { status: response.status, headers: noStoreHeaders });
  } catch { return failure(502); }
}
