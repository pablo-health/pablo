// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Shared building blocks for the per-provider Content-Security-Policy
 * headers in `firebase/middleware.ts` and `oidc/middleware.ts`: a per-request
 * nonce for script-src (replacing `'unsafe-inline'`) and a guard that keeps a
 * misconfigured API_URL from ever being interpolated into connect-src.
 */

import type { NextRequest } from "next/server"

export const NONCE_HEADER = "x-nonce"

/**
 * Stripe.js, for collecting a card without the card touching this application.
 *
 * It belongs in two directives and only two. `script-src` loads the library
 * itself; `frame-src` lets it open the iframe that actually holds the card
 * fields, which is the whole point — the number is typed into a document this
 * origin cannot read.
 *
 * Deliberately NOT in `connect-src`. Stripe.js does not call `api.stripe.com`
 * from this page; it routes API traffic through that same iframe, which runs
 * on Stripe's origin under Stripe's own policy rather than ours. Removing
 * `api.stripe.com` from a working policy changes nothing observable, so
 * allowing it here would widen egress on every page in the app in exchange
 * for nothing. If a future flow genuinely needs a host — a 3-D Secure
 * challenge renders from `hooks.stripe.com` — the browser names it in one
 * line, and it gets added then, with the evidence.
 */
export const STRIPE_JS = "https://js.stripe.com"

// Browsers already treat http://localhost as a potentially trustworthy
// origin, and local dev points API_URL at a plain-HTTP backend (see
// frontend/.env.example), so loopback hosts are exempt from the https
// requirement below.
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"])

/**
 * Validates that an env-provided URL is an https origin before it gets
 * interpolated into connect-src. Returns the normalized origin (no path),
 * or "" when `value` is unset. Throws at module load time — i.e. at
 * middleware startup — when the value is present but not a valid https
 * origin, so a bad deployment config fails loudly instead of shipping a
 * connect-src that quietly allows a plaintext endpoint.
 */
export function assertHttpsOrigin(name: string, value: string): string {
  if (!value) return ""

  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    throw new Error(`${name} must be a valid absolute URL, got "${value}"`)
  }

  if (parsed.protocol !== "https:" && !LOOPBACK_HOSTS.has(parsed.hostname)) {
    throw new Error(`${name} must be an https origin, got "${value}"`)
  }

  return parsed.origin
}

/**
 * Per-request nonce for script-src / the theme bootstrap script, following
 * https://nextjs.org/docs/app/guides/content-security-policy.
 */
export function generateNonce(): string {
  return Buffer.from(crypto.randomUUID()).toString("base64")
}

/**
 * Request headers carrying both the nonce (for our own Server Components,
 * read back via `headers()`) and the resolved CSP (so Next.js can thread the
 * same nonce onto the scripts it injects itself). Pass the result as
 * `NextResponse.next({ request: { headers } })`.
 */
export function requestHeadersWithNonce(
  request: NextRequest,
  csp: string,
  nonce: string
): Headers {
  const headers = new Headers(request.headers)
  headers.set(NONCE_HEADER, nonce)
  headers.set("Content-Security-Policy", csp)
  return headers
}
