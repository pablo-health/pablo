// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { clinicianNavigation } from "../sidebarExtensions"

describe("clinicianNavigation", () => {
  it("keeps Refills dark until the deployment turns it on", () => {
    const refills = clinicianNavigation.find((item) => item.href === "/dashboard/refills")
    expect(refills?.requiresFlag).toBe("refill_requests")
  })
})
