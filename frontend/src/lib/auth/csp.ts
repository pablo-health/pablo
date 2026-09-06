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
 * These are the hosts Stripe documents for a Stripe.js + Elements integration
 * (https://docs.stripe.com/security/guide — "Content Security Policy"), minus
 * the ones belonging to products this application does not use: Checkout,
 * Connect embedded components, Link, the crypto onramp, and the Address
 * Element (whose `maps.googleapis.com` entries are only needed with a Google
 * Maps key of our own).
 *
 * The wildcard is not laziness. Stripe.js starts frames on `*.js.stripe.com`
 * origins "where possible" to improve performance, which means whether it
 * does so on any given load is Stripe's decision, not ours — an integration
 * that allows only the apex host works until the day it doesn't. That is
 * worth stating plainly, because a local probe mounting cleanly without the
 * wildcard is evidence about one page load and nothing more.
 *
 * `hooks.stripe.com` renders a 3-D Secure challenge. Card payments in scope
 * for SCA get one, so a policy without it collects cards happily and then
 * fails the first authentication a bank asks for.
 *
 * `api.stripe.com` is where `confirmSetup` posts. Mounting an Element does
 * not need it — the mount path talks to Stripe from inside Stripe's own
 * frame, under Stripe's policy rather than ours — so this one cannot be
 * verified by watching a card field appear. It is here because Stripe
 * documents it for the submit path.
 */
export const STRIPE_SCRIPT_SRC = "https://js.stripe.com https://*.js.stripe.com"
export const STRIPE_FRAME_SRC =
  "https://js.stripe.com https://*.js.stripe.com https://hooks.stripe.com"
export const STRIPE_CONNECT_SRC = "https://api.stripe.com"

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
