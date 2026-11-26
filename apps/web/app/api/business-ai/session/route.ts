import { NextRequest, NextResponse } from "next/server";
import { AI_COOKIE, aiConfig, createAiSession, noStoreHeaders, safeEqual, sameOrigin, validAiSession } from "@/lib/businessAiServer";

export const runtime = "nodejs";

export function GET(request: NextRequest) {
  const config = aiConfig();
  return NextResponse.json({ enabled: config.enabled, authenticated: config.enabled
    && validAiSession(request.cookies.get(AI_COOKIE)?.value, config.sessionSecret) }, { headers: noStoreHeaders });
}

export async function POST(request: NextRequest) {
  const config = aiConfig();
  if (!config.enabled) return NextResponse.json({ error: "Comprefácil AI no está disponible." }, { status: 404, headers: noStoreHeaders });
  if (!sameOrigin(request)) return NextResponse.json({ error: "Solicitud no permitida." }, { status: 403, headers: noStoreHeaders });
  const body: unknown = await request.json().catch(() => null);
  const code = body && typeof body === "object" && "code" in body ? (body as { code: unknown }).code : null;
  if (typeof code !== "string" || code.length > 200 || !safeEqual(code, config.code)) {
    return NextResponse.json({ error: "Código de acceso incorrecto." }, { status: 401, headers: noStoreHeaders });
  }
  const response = NextResponse.json({ authenticated: true }, { headers: noStoreHeaders });
  response.cookies.set(AI_COOKIE, createAiSession(config.sessionSecret), {
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
  if (!validAiSession(request.cookies.get(AI_COOKIE)?.value, config.sessionSecret)) {
    return NextResponse.json({ error: "La sesión terminó. Ingresa de nuevo." }, { status: 401, headers: noStoreHeaders });
  }
  const response = NextResponse.json({ reset: true }, { headers: noStoreHeaders });
  response.cookies.set(AI_COOKIE, createAiSession(config.sessionSecret), {
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
