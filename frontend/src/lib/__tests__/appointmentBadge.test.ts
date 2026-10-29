// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { appointmentBadge } from "../appointmentBadge"

describe("appointmentBadge", () => {
  it("reads Scheduled before a session starts", () => {
    expect(
      appointmentBadge({ status: "confirmed", session_id: null, session_status: null })?.label,
    ).toBe("Scheduled")
  })

  it.each([
    ["in_progress", "In session"],
    ["recording_complete", "Drafting note"],
    ["transcribing", "Drafting note"],
    ["processing", "Drafting note"],
    ["pending_review", "To review"],
    ["finalized", "Signed"],
    ["failed", "Draft failed"],
  ] as const)("follows the linked session once it is %s", (sessionStatus, label) => {
    expect(
      appointmentBadge({ status: "confirmed", session_id: "s1", session_status: sessionStatus })
        ?.label,
    ).toBe(label)
  })

  it("keeps a cancelled or no-show appointment as it is", () => {
    expect(
      appointmentBadge({ status: "no_show", session_id: "s1", session_status: "finalized" })?.label,
    ).toBe("No-show")
    expect(
      appointmentBadge({ status: "cancelled", session_id: "s1", session_status: "pending_review" })
        ?.label,
    ).toBe("Cancelled")
  })

  it("falls back to the appointment when the session status is unknown", () => {
    expect(
      appointmentBadge({ status: "completed", session_id: "s1", session_status: undefined })?.label,
    ).toBe("Done")
    expect(
      appointmentBadge({ status: "confirmed", session_id: "s1", session_status: "cancelled" })
        ?.label,
    ).toBe("Scheduled")
  })
})
