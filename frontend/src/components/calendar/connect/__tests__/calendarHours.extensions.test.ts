// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { useHoursFromTextAvailable } from "../calendarHours.extensions"

describe("calendarHours.extensions", () => {
  it("lets every account describe its hours in the default build", () => {
    // A plain function in the default build, so it can be called outside a render.
    expect(useHoursFromTextAvailable()).toBe(true)
  })
})
