// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Display helpers for the audit trail.
 *
 * The load-bearing one is `formatAuditAction`: it has to make every action
 * the server can emit readable without a per-action label list that would
 * drift out of step with it.
 */

import { describe, it, expect } from "vitest"
import {
  describeAuditResource,
  formatAuditAction,
  formatAuditTimestamp,
  isSomeoneElsesAction,
  summarizeUserAgent,
} from "../auditLogDisplay"
import type { AuditLogItem } from "@/lib/api/users"

function entry(overrides: Partial<AuditLogItem> = {}): AuditLogItem {
  return {
    id: "row-1",
    timestamp: "2026-03-01T12:30:00Z",
    actor_type: "clinician",
    action: "patient_viewed",
    resource_type: "patient",
    resource_id: "patient-123",
    patient_id: "patient-123",
    session_id: null,
    ip_address: "203.0.113.4",
    user_agent: "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    ...overrides,
  }
}

describe("formatAuditAction", () => {
  it("reads an action as a sentence", () => {
    expect(formatAuditAction("patient_viewed")).toBe("Patient viewed")
    expect(formatAuditAction("session_transcript_uploaded")).toBe("Session transcript uploaded")
  })

  it("keeps acronyms spelled the way they are said", () => {
    expect(formatAuditAction("session_note_generated")).toBe("Session note generated")
    expect(formatAuditAction("soap_note_viewed")).toBe("SOAP note viewed")
    expect(formatAuditAction("baa_accepted")).toBe("BAA accepted")
    expect(formatAuditAction("ical_calendar_synced")).toBe("iCal calendar synced")
  })

  it("survives an action it has never seen", () => {
    // The point of the generic formatter: a new server-side action ships
    // without a frontend change, and still reads as English.
    expect(formatAuditAction("some_future_thing_happened")).toBe("Some future thing happened")
  })

  it("leaves something unsplittable alone", () => {
    expect(formatAuditAction("")).toBe("")
    expect(formatAuditAction("login")).toBe("Login")
  })
})

describe("summarizeUserAgent", () => {
  it("names the browser", () => {
    expect(summarizeUserAgent(entry().user_agent)).toBe("Chrome")
  })

  it("distinguishes Edge from the Chrome string it contains", () => {
    expect(
      summarizeUserAgent("Mozilla/5.0 (Windows NT 10.0) Chrome/131.0.0.0 Safari/537.36 Edg/131.0"),
    ).toBe("Edge")
  })

  it("names the companion", () => {
    expect(summarizeUserAgent("Pablo-Companion/1.4 (macOS 15.2)")).toBe("Pablo Companion")
  })

  it("shortens something it does not recognise rather than dropping it", () => {
    const odd = "x".repeat(80)
    const summary = summarizeUserAgent(odd)
    expect(summary).toHaveLength(41)
    expect(summary.endsWith("…")).toBe(true)
  })

  it("has something to show when there was no user agent", () => {
    expect(summarizeUserAgent(null)).toBe("—")
  })
})

describe("describeAuditResource", () => {
  it("names the kind of record and its id, never a person", () => {
    expect(describeAuditResource(entry())).toBe("Patient patient-123")
  })
})

describe("isSomeoneElsesAction", () => {
  it("is false for the account holder's own work", () => {
    expect(isSomeoneElsesAction(entry())).toBe(false)
  })

  it("is true for a row written into your trail by someone else", () => {
    // A public booking writes into the practice owner's trail.
    expect(isSomeoneElsesAction(entry({ actor_type: "anonymous" }))).toBe(true)
    expect(isSomeoneElsesAction(entry({ actor_type: "system" }))).toBe(true)
  })
})

describe("formatAuditTimestamp", () => {
  it("renders an absolute time", () => {
    const formatted = formatAuditTimestamp("2026-03-01T12:30:00Z")
    expect(formatted).toContain("2026")
    expect(formatted).not.toContain("ago")
  })

  it("shows an unparseable timestamp as it came, rather than 'Invalid Date'", () => {
    expect(formatAuditTimestamp("not-a-date")).toBe("not-a-date")
  })
})
