// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Fill the stand-in name server (scripts/fake_dns.py) that the backend asks
 * when a practice checks its DNS records. The compose stack points the backend
 * at it with `PRACTICE_DOMAIN_DNS_NAMESERVERS`, so a spec can publish a record
 * and see the page find it, without touching real DNS.
 */

import { DNS_URL } from "./stack"

async function call(method: "PUT" | "POST", path: string, body?: unknown): Promise<void> {
  const response = await fetch(`${DNS_URL}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`fake dns ${method} ${path} → ${response.status}`)
  }
}

export const dns = {
  /** Answer `name` with these values for `type`. An empty list removes them. */
  async set(name: string, type: "A" | "AAAA" | "CNAME" | "TXT", values: string[]): Promise<void> {
    await call("PUT", "/_fake/records", { name, type, values })
  },
}
