// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import type { NoteSignature, NoteSigningRecord } from "@/types/notes"
import { formatSignedAt, pdfSignatureBlock, signedByLine } from "../signatureBlock"

function version(overrides: Partial<NoteSignature> = {}): NoteSignature {
  return {
    id: "sig-1",
    version: 1,
    signed_by: "user-1",
    signer_name: "Sam Ortiz",
    signer_credentials: "LMFT",
    signed_at: "2026-10-05T19:04:00Z",
    unlocked_at: null,
    unlocked_by: null,
    unlock_reason: null,
    note_type: "soap",
    note_type_version: null,
    content: null,
    content_edited: null,
    ...overrides,
  }
}

function record(overrides: Partial<NoteSigningRecord> = {}): NoteSigningRecord {
  return {
    note_id: "note-1",
    finalized_at: null,
    signature: null,
    versions: [],
    addenda: [],
    ...overrides,
  }
}

describe("signedByLine", () => {
  it("joins the name and credentials", () => {
    expect(signedByLine("Sam Ortiz", "LMFT")).toBe("Electronically signed by Sam Ortiz, LMFT")
  })

  it("leaves off empty credentials", () => {
    expect(signedByLine("Sam Ortiz", null)).toBe("Electronically signed by Sam Ortiz")
    expect(signedByLine("Sam Ortiz", "  ")).toBe("Electronically signed by Sam Ortiz")
  })
})

describe("formatSignedAt", () => {
  it("names the zone it is shown in", () => {
    expect(formatSignedAt("2026-10-05T19:04:00Z", "America/New_York")).toBe(
      "Oct 5, 2026, 3:04 PM EDT",
    )
    expect(formatSignedAt("2026-10-05T19:04:00Z", "America/Los_Angeles")).toBe(
      "Oct 5, 2026, 12:04 PM PDT",
    )
  })
})

describe("pdfSignatureBlock", () => {
  const tz = "America/New_York"

  it("is nothing for an unsigned note with no history", () => {
    expect(pdfSignatureBlock(record(), tz)).toBeUndefined()
    expect(pdfSignatureBlock(undefined, tz)).toBeUndefined()
  })

  it("is the signature and its date for a signed note", () => {
    const current = version()
    const block = pdfSignatureBlock(
      record({ finalized_at: current.signed_at, signature: current, versions: [current] }),
      tz,
    )
    expect(block).toEqual({
      lines: ["Electronically signed by Sam Ortiz, LMFT", "Oct 5, 2026, 3:04 PM EDT"],
      amendments: [],
      addenda: [],
    })
  })

  it("is Finalized with no signature line for a note finalized before signatures", () => {
    const block = pdfSignatureBlock(record({ finalized_at: "2024-01-15T14:30:00Z" }), tz)
    expect(block?.lines).toEqual(["Finalized Jan 15, 2024"])
  })

  it("adds an amendment line per unlock and each addendum's own signature", () => {
    const first = version({
      unlocked_at: "2026-10-06T14:00:00Z",
      unlock_reason: "Wrong date of service",
    })
    const second = version({ id: "sig-2", version: 2, signer_credentials: "LMFT, LPCC" })
    const block = pdfSignatureBlock(
      record({
        finalized_at: second.signed_at,
        signature: second,
        versions: [first, second],
        addenda: [
          {
            id: "a-1",
            text: "Called after.",
            signer_name: "Sam Ortiz",
            signer_credentials: null,
            created_by: "user-1",
            created_at: "2026-10-05T20:00:00Z",
          },
        ],
      }),
      tz,
    )
    expect(block?.lines[0]).toBe("Electronically signed by Sam Ortiz, LMFT, LPCC")
    expect(block?.amendments).toEqual(["Amended Oct 6, 2026: Wrong date of service"])
    expect(block?.addenda).toEqual([
      {
        text: "Called after.",
        lines: ["Electronically signed by Sam Ortiz", "Oct 5, 2026, 4:00 PM EDT"],
      },
    ])
  })
})
