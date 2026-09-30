// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { clinicianNavigation } from "../sidebarExtensions"

describe("clinicianNavigation", () => {
  it("keeps Refills dark until the deployment turns it on", () => {
    const refills = clinicianNavigation.find((item) => item.href === "/dashboard/refills")
    expect(refills?.requiresFlag).toBe("refill_requests")
  })

  it("has one Inbox, ungated, right after Dashboard, and it carries the only badge", () => {
    expect(clinicianNavigation.map((item) => item.name).slice(0, 2)).toEqual(["Dashboard", "Inbox"])
    const inbox = clinicianNavigation.find((item) => item.href === "/dashboard/inbox")
    expect(inbox?.requiresFlag).toBeUndefined()
    expect(inbox?.requiresCapability).toBeUndefined()
    expect(clinicianNavigation.filter((item) => item.badge)).toEqual([inbox])
    expect(inbox?.badge).toBe("inbox")
  })

  it("no longer has a Messages item of its own", () => {
    expect(clinicianNavigation.map((item) => item.href)).not.toContain("/dashboard/messages")
    expect(clinicianNavigation.map((item) => item.name)).not.toContain("Messages")
  })
})
