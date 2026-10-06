// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NoteConsentLine — the client's answer about AI-assisted notes, on a session
 * note, read from the consent record.
 *
 * With the practice asking: a dated line when an answer is on file, "No
 * consent on file" and a way to record one when it is not. With the practice
 * not asking: nothing, and the record is not even fetched.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { NoteConsentLine } from "../NoteConsentLine"
import type { AiConsentEntry, AiConsentRecord } from "@/types/aiConsent"

const mockUseAiConsent = vi.fn()
const mockAsks = vi.fn()

vi.mock("@/hooks/useAiConsent", () => ({
  useAiConsent: (...args: unknown[]) => mockUseAiConsent(...args),
  useAsksClientsAboutAiNotes: () => mockAsks(),
  useRecordAiConsent: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

function entry(overrides: Partial<AiConsentEntry>): AiConsentEntry {
  return {
    id: "e1",
    decision: "consented",
    effective_on: "2026-10-06",
    source: "clinician",
    recorded_by_name: "Dr. Rivera",
    recorded_at: "2026-10-06T15:00:00Z",
    ...overrides,
  }
}

function given(asks: boolean, record: AiConsentRecord | undefined) {
  mockAsks.mockReturnValue(asks)
  mockUseAiConsent.mockReturnValue({ data: record })
}

describe("NoteConsentLine", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the dated answer when the client agreed", () => {
    const agreed = entry({ effective_on: "2026-10-06" })
    given(true, { current: agreed, history: [agreed] })

    render(<NoteConsentLine patientId="p1" />)

    expect(screen.getByTestId("note-consent-line")).toHaveTextContent(
      "Client agreed to AI-assisted notes on Oct 6, 2026",
    )
    expect(screen.queryByRole("button", { name: "Record consent" })).not.toBeInTheDocument()
    expect(mockUseAiConsent).toHaveBeenCalledWith("p1")
  })

  it("shows the dated answer when the client declined", () => {
    const declined = entry({ decision: "declined", effective_on: "2026-11-02" })
    given(true, { current: declined, history: [declined] })

    render(<NoteConsentLine patientId="p1" />)

    expect(screen.getByTestId("note-consent-line")).toHaveTextContent(
      "Client declined AI-assisted notes on Nov 2, 2026",
    )
  })

  it("says no consent is on file, and opens the dialog to record one", async () => {
    given(true, { current: null, history: [] })

    render(<NoteConsentLine patientId="p1" />)

    expect(screen.getByTestId("note-consent-line")).toHaveTextContent("No consent on file")
    await userEvent.click(screen.getByRole("button", { name: "Record consent" }))
    expect(screen.getByRole("dialog", { name: "AI notes" })).toBeInTheDocument()
  })

  it("shows nothing when the practice does not ask, and does not read the record", () => {
    given(false, undefined)

    const { container } = render(<NoteConsentLine patientId="p1" />)

    expect(container).toBeEmptyDOMElement()
    expect(mockUseAiConsent).toHaveBeenCalledWith(undefined)
  })

  it("shows nothing until the record has loaded", () => {
    given(true, undefined)

    const { container } = render(<NoteConsentLine patientId="p1" />)

    expect(container).toBeEmptyDOMElement()
  })
})
