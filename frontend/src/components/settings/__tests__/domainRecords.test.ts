// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The record helpers behind Settings > Domains: the Host a DNS provider's
 * form asks for, carrying a check's results across a poll, and when a host
 * is the server's to finish.
 */

import { describe, expect, it } from "vitest"

import { isFinishingSetup, relativeHost, withLastCheck } from "../domains/records"
import type { PracticeDomain } from "@/lib/api/practiceDomains"

describe("relativeHost", () => {
  it("is @ for the registrable domain itself", () => {
    expect(relativeHost("example.com", "example.com")).toBe("@")
  })

  it("drops the domain from a name under it", () => {
    expect(relativeHost("portal.example.com", "example.com")).toBe("portal")
    expect(relativeHost("_pablo-verify.example.com", "example.com")).toBe("_pablo-verify")
    expect(relativeHost("_acme-challenge.portal.example.com", "example.com")).toBe("_acme-challenge.portal")
  })

  it("drops the whole of a multi-label suffix", () => {
    expect(relativeHost("example.co.uk", "example.co.uk")).toBe("@")
    expect(relativeHost("portal.example.co.uk", "example.co.uk")).toBe("portal")
    expect(relativeHost("k1._domainkey.example.co.uk", "example.co.uk")).toBe("k1._domainkey")
  })

  it("ignores case and a trailing dot", () => {
    expect(relativeHost("Portal.Example.com.", "example.com")).toBe("portal")
    expect(relativeHost("example.com", "EXAMPLE.COM.")).toBe("@")
  })

  it("gives back the full name when it is not under the domain, or there is no domain", () => {
    expect(relativeHost("portal.example.org", "example.com")).toBe("portal.example.org")
    // A shared ending is not being under it.
    expect(relativeHost("notexample.com", "example.com")).toBe("notexample.com")
    expect(relativeHost("portal.example.com", null)).toBe("portal.example.com")
    expect(relativeHost("portal.example.com", undefined)).toBe("portal.example.com")
    expect(relativeHost("portal.example.com", "")).toBe("portal.example.com")
  })
})

const CNAME = { type: "CNAME", name: "portal.example.com", value: "sites.example.net" }
const TXT = { type: "TXT", name: "_pablo-verify.example.com", value: "pablo-verify=token" }

function host(overrides: Partial<PracticeDomain> = {}): PracticeDomain {
  return {
    domain: "portal.example.com",
    purpose: "portal",
    status: "verifying",
    is_primary: false,
    verified_at: null,
    created_at: "2026-09-01T00:00:00Z",
    dns_records: [CNAME, TXT],
    apex: "example.com",
    ...overrides,
  }
}

describe("withLastCheck", () => {
  const checked = {
    domains: [
      host({
        dns_records: [
          { ...CNAME, check: "ok" as const, found: ["sites.example.net"] },
          { ...TXT, check: "missing" as const, found: [] },
        ],
      }),
    ],
  }

  it("puts a check's results back on a later list of the same host", () => {
    const merged = withLastCheck({ domains: [host()] }, checked)
    expect(merged.domains[0].dns_records.map((r) => r.check)).toEqual(["ok", "missing"])
  })

  it("drops them once the host's status has moved", () => {
    const merged = withLastCheck({ domains: [host({ status: "active" })] }, checked)
    expect(merged.domains[0].dns_records.map((r) => r.check)).toEqual([undefined, undefined])
  })

  it("leaves the list alone with no check", () => {
    const list = { domains: [host()] }
    expect(withLastCheck(list, undefined)).toBe(list)
  })
})

describe("isFinishingSetup", () => {
  const allFound = [
    { ...CNAME, check: "ok" as const },
    { ...TXT, check: "ok" as const },
  ]

  it("is a host still in progress with every record found", () => {
    expect(isFinishingSetup(host({ dns_records: allFound }))).toBe(true)
    expect(isFinishingSetup(host({ status: "pending", dns_records: allFound }))).toBe(true)
  })

  it("is not a host with a record still to add, unchecked, active, failed, or stuck", () => {
    expect(isFinishingSetup(host({ dns_records: [allFound[0], { ...TXT, check: "missing" }] }))).toBe(false)
    expect(isFinishingSetup(host())).toBe(false)
    expect(isFinishingSetup(host({ status: "active", dns_records: allFound }))).toBe(false)
    expect(isFinishingSetup(host({ status: "error", dns_records: allFound }))).toBe(false)
    expect(isFinishingSetup(host({ stuck: true, dns_records: allFound }))).toBe(false)
    expect(isFinishingSetup(host({ dns_records: [] }))).toBe(false)
  })
})
