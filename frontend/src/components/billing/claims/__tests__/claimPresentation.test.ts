// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The next-action copy: one line per value the API sends, and nothing at all
 * for the paid claim it sends null for.
 */

import { describe, expect, it } from "vitest"
import type { NextAction } from "@/types/claims"
import { presentNextAction } from "../claimPresentation"

const COPY: Record<NextAction, string> = {
  review_and_file: "Review and file",
  queued_to_send: "Queued to send",
  sending: "Sending",
  await_acknowledgment: "Waiting for the clearinghouse to acknowledge it",
  await_payer: "Waiting for the payer to accept it",
  await_remittance: "Waiting for the remittance",
  review_remittance: "Review the remittance; correct or appeal",
  correct_and_resubmit: "Fix and refile",
  appeal_or_correct: "Correct and resubmit, or appeal",
  check_with_clearinghouse: "No receipt in time; check with the clearinghouse",
}

describe("presentNextAction", () => {
  it.each(Object.entries(COPY))("reads %s as its own line of copy", (value, expected) => {
    expect(presentNextAction(value as NextAction)).toBe(expected)
  })

  it("asks for nothing on a paid claim", () => {
    expect(presentNextAction(null)).toBeNull()
  })
})
