import "server-only";

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";

export const AI_COOKIE = "cf_ai_session";
const SESSION_SECONDS = 60 * 60 * 8;

export function aiConfig() {
  const code = process.env.BUSINESS_AI_UI_ACCESS_CODE ?? "";
  const sessionSecret = process.env.BUSINESS_AI_SESSION_SECRET ?? "";
  const backendToken = process.env.BUSINESS_AGENT_ACCESS_TOKEN ?? "";
  const backendUrl = process.env.BUSINESS_AI_API_URL ?? "";
  return {
    enabled: process.env.BUSINESS_AI_UI_ENABLED === "true"
      && code.length >= 32 && sessionSecret.length >= 32 && backendToken.length >= 32
      && /^https?:\/\//.test(backendUrl),
    code, sessionSecret, backendToken, backendUrl,
  };
}

export function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

function signature(expires: string, nonce: string, secret: string): string {
  return createHmac("sha256", secret).update(`v2.${expires}.${nonce}`).digest("hex");
}

export function createAiSession(secret: string, now = Date.now()): string {
  const expires = String(Math.floor(now / 1000) + SESSION_SECONDS);
  const nonce = randomBytes(24).toString("hex");
  return `v2.${expires}.${nonce}.${signature(expires, nonce, secret)}`;
}

export function validAiSession(cookie: string | undefined, secret: string, now = Date.now()): boolean {
  if (!cookie || !secret) return false;
  const parts = cookie.split(".");
  if (parts.length !== 4 || parts[0] !== "v2" || !/^\d{10}$/.test(parts[1])
    || !/^[a-f0-9]{48}$/.test(parts[2]) || !/^[a-f0-9]{64}$/.test(parts[3])) return false;
  const expires = Number(parts[1]);
  if (expires <= Math.floor(now / 1000) || expires > Math.floor(now / 1000) + SESSION_SECONDS) return false;
  return safeEqual(parts[3], signature(parts[1], parts[2], secret));
}

export function conversationSession(cookie: string, secret: string): string {
  return createHmac("sha256", secret).update(`conversation.${cookie}`).digest("hex");
}

export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    const parsed = new URL(origin);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.host === host;
  } catch {
    return false;
  }
}

export const noStoreHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
