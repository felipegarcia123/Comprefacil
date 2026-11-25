import { NextRequest, NextResponse } from "next/server";
import { isAgentReply } from "@/lib/businessAi";
import { AI_COOKIE, aiConfig, noStoreHeaders, sameOrigin, validAiSession } from "@/lib/businessAiServer";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const config = aiConfig();
  if (!config.enabled) return NextResponse.json({ error: "Comprefácil AI no está disponible." }, { status: 404, headers: noStoreHeaders });
  if (!sameOrigin(request)) return NextResponse.json({ error: "Solicitud no permitida." }, { status: 403, headers: noStoreHeaders });
  if (!validAiSession(request.cookies.get(AI_COOKIE)?.value, config.sessionSecret)) {
    return NextResponse.json({ error: "La sesión de Comprefácil AI terminó. Ingresa de nuevo." }, { status: 401, headers: noStoreHeaders });
  }
  const body: unknown = await request.json().catch(() => null);
  const question = body && typeof body === "object" && "question" in body ? (body as { question: unknown }).question : null;
  if (typeof question !== "string" || question.trim().length < 3 || question.length > 600) {
    return NextResponse.json({ error: "Escribe una consulta de 3 a 600 caracteres." }, { status: 400, headers: noStoreHeaders });
  }
  try {
    const upstream = await fetch(config.backendUrl, {
      method: "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(35000),
      headers: { "Authorization": `Bearer ${config.backendToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ question: question.trim() }),
    });
    if (!upstream.ok) {
      const message = upstream.status === 429 ? "La consulta excedió los límites. Intenta una pregunta más concreta."
        : upstream.status === 401 || upstream.status === 503 ? "Comprefácil AI no está configurado para responder ahora."
        : "No se pudo completar la consulta. Intenta nuevamente.";
      return NextResponse.json({ error: message }, { status: upstream.status === 429 ? 429 : 502, headers: noStoreHeaders });
    }
    const answer: unknown = await upstream.json();
    if (!isAgentReply(answer)) throw new Error("Invalid agent response");
    return NextResponse.json({ answer: answer.answer, tools_used: answer.tools_used,
      periods: answer.periods, limitations: answer.limitations, usage: answer.usage,
      duration_ms: answer.duration_ms }, { headers: noStoreHeaders });
  } catch {
    return NextResponse.json({ error: "No se pudo conectar con Comprefácil AI. Intenta nuevamente." }, { status: 502, headers: noStoreHeaders });
  }
}
