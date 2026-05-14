// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Sanitize a user-supplied redirect target so it can only point to a
 * same-origin path inside Pablo. Blocks `javascript:` (XSS via href
 * click), `data:`, `http(s):` (open redirect / token exfiltration),
 * and `//evil.example` (protocol-relative open redirect).
 *
 * A valid target must start with a single `/` followed by something
 * other than `/`.
 */
export function safeRedirectPath(url: string | null | undefined, fallback: string): string {
  if (url && /^\/(?!\/)/.test(url)) return url
  return fallback
}
