// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Runtime reverse-proxy for the Firebase auth helper.
 *
 * The Firebase JS SDK loads its OAuth helper from `<authDomain>/__/auth/*`
 * and its config from `/__/firebase/init.json`. To let `authDomain` be our
 * own domain (so the Google consent screen reads "continue to <our domain>"
 * rather than "<project>.firebaseapp.com"), those reserved paths must be
 * served from this app. `next.config.ts` rewrites `/__/auth/*` and
 * `/__/firebase/*` here; this handler proxies to the Firebase-hosted helper.
 *
 * Why a route handler and not a `next.config` rewrite to the upstream:
 * `rewrites()` is evaluated at BUILD time (FIREBASE_PROJECT_ID is empty
 * then), and the same frontend image is promoted dev->prod — so a
 * build-time target would be wrong for one environment. A route handler
 * runs on the Node runtime and reads the env at REQUEST time, so the same
 * image proxies to the correct project per deployment.
 *
 * `/__/auth/action` is intentionally NOT proxied — it's a real local page
 * (handled by an earlier rewrite). Only handler/iframe/init.json etc. land
 * here.
 */

import { type NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function helperOrigin(): string | null {
  const explicit = process.env.FIREBASE_AUTH_HELPER_ORIGIN;
  if (explicit) return explicit.replace(/\/+$/, "");
  const projectId =
    process.env.FIREBASE_PROJECT_ID || process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
  return projectId ? `https://${projectId}.firebaseapp.com` : null;
}

// Headers that must not be forwarded verbatim across a proxy hop. We also
// drop content-encoding/content-length because fetch decodes the upstream
// body, so the original values would no longer match what we stream back.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
  "host",
]);

async function proxy(req: NextRequest, segments: string[]): Promise<Response> {
  const origin = helperOrigin();
  if (!origin) {
    return new Response("Firebase auth helper origin not configured", { status: 503 });
  }

  // segments e.g. ["auth", "handler"] -> https://<project>.firebaseapp.com/__/auth/handler
  const target = `${origin}/__/${segments.join("/")}${req.nextUrl.search}`;

  const fwdHeaders = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) fwdHeaders.set(key, value);
  });
  fwdHeaders.set("host", new URL(origin).host);

  const init: RequestInit = {
    method: req.method,
    headers: fwdHeaders,
    redirect: "manual",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  const upstream = await fetch(target, init);

  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) respHeaders.set(key, value);
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
}

// `params` is a Promise in Next 15+/16; awaiting a plain object is a no-op,
// so this stays correct across versions.
type Ctx = { params: Promise<{ segments: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(req, (await ctx.params).segments);
}

export async function POST(req: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(req, (await ctx.params).segments);
}

export async function HEAD(req: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(req, (await ctx.params).segments);
}

export async function OPTIONS(req: NextRequest, ctx: Ctx): Promise<Response> {
  return proxy(req, (await ctx.params).segments);
}
