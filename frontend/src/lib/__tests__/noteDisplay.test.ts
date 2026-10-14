// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { noteStatus } from "../noteDisplay"
import { createMockNote } from "@/test/factories"

describe("noteStatus", () => {
  it("never calls an imported note a draft: it was signed where it was written", () => {
    expect(noteStatus(createMockNote({ finalized_at: null, imported: true })).label).toBe(
      "Imported",
    )
  })

  it("keeps draft and finalized for every other note", () => {
    expect(noteStatus(createMockNote({ finalized_at: null })).label).toBe("Draft")
    expect(noteStatus(createMockNote({ finalized_at: "2026-08-19T15:00:00Z" })).label).toBe(
      "Finalized",
    )
  })
})
