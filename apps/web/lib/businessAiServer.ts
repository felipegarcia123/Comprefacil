import "server-only";

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";

export const AI_COOKIE = "cf_ai_session";
const SESSION_SECONDS = 60 * 60 * 8;
export type AiActor = { id: string; business_id: string; user_id: string; access_code: string; api_token: string };

function notificationActors(businessId: string): AiActor[] {
  try {
    const values: unknown = JSON.parse(process.env.BUSINESS_NOTIFICATIONS_ACTORS_JSON ?? "[]");
    if (!Array.isArray(values) || values.length > 100) return [];
    const actors = values.filter((item): item is AiActor => {
      if (!item || typeof item !== "object") return false;
      const actor = item as Record<string, unknown>;
      return typeof actor.id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(actor.id)
        && actor.business_id === businessId && typeof actor.user_id === "string"
        && /^[a-fA-F0-9-]{36}$/.test(actor.user_id)
        && typeof actor.access_code === "string" && actor.access_code.length >= 32
        && typeof actor.api_token === "string" && actor.api_token.length >= 32;
    });
    return actors.length === values.length
      && new Set(actors.map(item => item.id)).size === actors.length
      && new Set(actors.map(item => item.access_code)).size === actors.length ? actors : [];
  } catch { return []; }
}

export function aiConfig() {
  const code = process.env.BUSINESS_AI_UI_ACCESS_CODE ?? "";
  const sessionSecret = process.env.BUSINESS_AI_SESSION_SECRET ?? "";
  const backendToken = process.env.BUSINESS_AGENT_ACCESS_TOKEN ?? "";
  const backendUrl = process.env.BUSINESS_AI_API_URL ?? "";
  const notificationsUrl = process.env.BUSINESS_AI_NOTIFICATIONS_API_URL ?? "";
  const actors = notificationActors(process.env.BUSINESS_NOTIFICATIONS_BUSINESS_ID ?? "");
  return {
    enabled: process.env.BUSINESS_AI_UI_ENABLED === "true"
      && code.length >= 32 && sessionSecret.length >= 32 && backendToken.length >= 32
      && /^https?:\/\//.test(backendUrl),
    code, sessionSecret, backendToken, backendUrl,
    notificationsEnabled: process.env.BUSINESS_NOTIFICATIONS_ENABLED === "true"
      && !!process.env.BUSINESS_NOTIFICATIONS_BUSINESS_ID
      && /^https?:\/\//.test(notificationsUrl) && actors.length > 0,
    notificationsUrl, actors,
  };
}

export function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

function signature(parts: string[], secret: string): string {
  return createHmac("sha256", secret).update(parts.join(".")).digest("hex");
}

export function createAiSession(secret: string, now = Date.now(), actorId?: string): string {
  const expires = String(Math.floor(now / 1000) + SESSION_SECONDS);
  const nonce = randomBytes(24).toString("hex");
  const parts = actorId ? ["v3", expires, nonce, actorId] : ["v2", expires, nonce];
  return `${parts.join(".")}.${signature(parts, secret)}`;
}

export function validAiSession(cookie: string | undefined, secret: string, now = Date.now()): boolean {
  if (!cookie || !secret) return false;
  const parts = cookie.split(".");
  if ((parts[0] === "v2" && parts.length !== 4) || (parts[0] === "v3" && parts.length !== 5)
    || !["v2", "v3"].includes(parts[0]) || !/^\d{10}$/.test(parts[1])
    || !/^[a-f0-9]{48}$/.test(parts[2])
    || (parts[0] === "v3" && !/^[A-Za-z0-9_-]{1,64}$/.test(parts[3]))) return false;
  const expires = Number(parts[1]);
  if (expires <= Math.floor(now / 1000) || expires > Math.floor(now / 1000) + SESSION_SECONDS) return false;
  const signed = parts.slice(0, -1);
  return /^[a-f0-9]{64}$/.test(parts.at(-1) ?? "") && safeEqual(parts.at(-1)!, signature(signed, secret));
}

export function aiActorForSession(cookie: string | undefined, config: ReturnType<typeof aiConfig>): AiActor | null {
  if (!config.notificationsEnabled || !validAiSession(cookie, config.sessionSecret) || !cookie?.startsWith("v3.")) return null;
  const actorId = cookie.split(".")[3];
  return config.actors.find(item => item.id === actorId) ?? null;
}

export function aiActorForCode(code: string, config: ReturnType<typeof aiConfig>): AiActor | null {
  if (!config.notificationsEnabled) return null;
  return config.actors.find(actor => safeEqual(code, actor.access_code)) ?? null;
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
