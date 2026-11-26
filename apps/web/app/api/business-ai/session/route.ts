import { NextRequest, NextResponse } from "next/server";
import { AI_COOKIE, aiActorForCode, aiActorForSession, aiConfig, createAiSession, noStoreHeaders, safeEqual, sameOrigin, validAiSession } from "@/lib/businessAiServer";

export const runtime = "nodejs";

export function GET(request: NextRequest) {
  const config = aiConfig();
  const cookie = request.cookies.get(AI_COOKIE)?.value;
  return NextResponse.json({ enabled: config.enabled, authenticated: config.enabled
    && validAiSession(cookie, config.sessionSecret),
    notifications_enabled: !!aiActorForSession(cookie, config) }, { headers: noStoreHeaders });
}

export async function POST(request: NextRequest) {
  const config = aiConfig();
  if (!config.enabled) return NextResponse.json({ error: "Comprefácil AI no está disponible." }, { status: 404, headers: noStoreHeaders });
  if (!sameOrigin(request)) return NextResponse.json({ error: "Solicitud no permitida." }, { status: 403, headers: noStoreHeaders });
  const body: unknown = await request.json().catch(() => null);
  const code = body && typeof body === "object" && "code" in body ? (body as { code: unknown }).code : null;
  const actor = typeof code === "string" ? aiActorForCode(code, config) : null;
  if (typeof code !== "string" || code.length > 200 || (!actor && !safeEqual(code, config.code))) {
    return NextResponse.json({ error: "Código de acceso incorrecto." }, { status: 401, headers: noStoreHeaders });
  }
  const response = NextResponse.json({ authenticated: true, notifications_enabled: !!actor }, { headers: noStoreHeaders });
  response.cookies.set(AI_COOKIE, createAiSession(config.sessionSecret, Date.now(), actor?.id), {
    httpOnly: true, sameSite: "strict",
    secure: process.env.NODE_ENV === "production" || request.nextUrl.protocol === "https:",
    path: "/api/business-ai", maxAge: 60 * 60 * 8,
  });
  return response;
}

export function PATCH(request: NextRequest) {
  const config = aiConfig();
  if (!config.enabled) return NextResponse.json({ error: "Comprefácil AI no está disponible." }, { status: 404, headers: noStoreHeaders });
  if (!sameOrigin(request)) return NextResponse.json({ error: "Solicitud no permitida." }, { status: 403, headers: noStoreHeaders });
  const cookie = request.cookies.get(AI_COOKIE)?.value;
  if (!validAiSession(cookie, config.sessionSecret)) {
    return NextResponse.json({ error: "La sesión terminó. Ingresa de nuevo." }, { status: 401, headers: noStoreHeaders });
  }
  const actor = aiActorForSession(cookie, config);
  const response = NextResponse.json({ reset: true, notifications_enabled: !!actor }, { headers: noStoreHeaders });
  response.cookies.set(AI_COOKIE, createAiSession(config.sessionSecret, Date.now(), actor?.id), {
    httpOnly: true, sameSite: "strict",
    secure: process.env.NODE_ENV === "production" || request.nextUrl.protocol === "https:",
    path: "/api/business-ai", maxAge: 60 * 60 * 8,
  });
  return response;
}

export function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ error: "Solicitud no permitida." }, { status: 403, headers: noStoreHeaders });
  const response = NextResponse.json({ authenticated: false }, { headers: noStoreHeaders });
  response.cookies.set(AI_COOKIE, "", { httpOnly: true, sameSite: "strict", path: "/api/business-ai", maxAge: 0 });
  return response;
}
