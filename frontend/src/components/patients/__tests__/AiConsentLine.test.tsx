// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AiConsentLine — the client's answer about AI-assisted notes, in the chart
 * header.
 *
 * Three states, each worded from the data: agreed on a day, declined on a
 * day, or not asked yet. Nothing renders until the answer has loaded, so
 * "not asked yet" is never shown in place of an answer still on its way.
 * The dialog records an answer — a choice and a date, today by default — and
 * shows the history.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AiConsentLine, formatConsentDate } from "../AiConsentLine"
import type { AiConsentEntry, AiConsentRecord } from "@/types/aiConsent"

const mockUseAiConsent = vi.fn()
const mockMutateAsync = vi.fn()

vi.mock("@/hooks/useAiConsent", () => ({
  useAiConsent: (...args: unknown[]) => mockUseAiConsent(...args),
  useRecordAiConsent: () => ({ mutateAsync: mockMutateAsync, isPending: false }),
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

function withRecord(record: AiConsentRecord | undefined) {
  mockUseAiConsent.mockReturnValue({ data: record })
}

function localToday(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

describe("AiConsentLine", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockMutateAsync.mockResolvedValue({ current: null, history: [] })
  })

  it("says the client agreed, and on what day", () => {
    const agreed = entry({ decision: "consented", effective_on: "2026-10-06" })
    withRecord({ current: agreed, history: [agreed] })

    render(<AiConsentLine patientId="p1" />)

    expect(screen.getByTestId("ai-consent-line")).toHaveTextContent(
      "AI notes: agreed Oct 6, 2026",
    )
  })

  it("says the client declined, and on what day", () => {
    const declined = entry({ decision: "declined", effective_on: "2026-11-02" })
    withRecord({ current: declined, history: [declined] })

    render(<AiConsentLine patientId="p1" />)

    expect(screen.getByTestId("ai-consent-line")).toHaveTextContent(
      "AI notes: declined Nov 2, 2026",
    )
  })

  it("says nobody has asked yet when there is no answer", () => {
    withRecord({ current: null, history: [] })

    render(<AiConsentLine patientId="p1" />)

    expect(screen.getByTestId("ai-consent-line")).toHaveTextContent("AI notes: not asked yet")
  })

  it("shows nothing while the answer is loading", () => {
    withRecord(undefined)

    render(<AiConsentLine patientId="p1" />)

    expect(screen.queryByTestId("ai-consent-line")).not.toBeInTheDocument()
  })

  it("records an answer for today by default", async () => {
    const user = userEvent.setup()
    withRecord({ current: null, history: [] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    expect(screen.getByLabelText("Date")).toHaveValue(localToday())
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()

    await user.click(screen.getByLabelText("Client agreed"))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "p1",
      data: { decision: "consented", effective_on: localToday() },
    })
  })

  it("records a declined answer for a chosen day", async () => {
    const user = userEvent.setup()
    withRecord({ current: null, history: [] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    await user.click(screen.getByLabelText("Client declined"))
    const date = screen.getByLabelText("Date")
    await user.clear(date)
    await user.type(date, "2026-09-30")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "p1",
      data: { decision: "declined", effective_on: "2026-09-30" },
    })
  })

  it("shows the server's refusal and keeps the dialog open", async () => {
    const user = userEvent.setup()
    mockMutateAsync.mockRejectedValue(new Error("The date can't be in the future."))
    withRecord({ current: null, history: [] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    await user.click(screen.getByLabelText("Client agreed"))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText("The date can't be in the future.")).toBeInTheDocument()
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("lists the history, newest first, with who recorded each answer", async () => {
    const user = userEvent.setup()
    const agreed = entry({ id: "a", decision: "consented", effective_on: "2026-10-06" })
    const declined = entry({
      id: "b",
      decision: "declined",
      effective_on: "2026-11-02",
      source: "intake_form",
      recorded_by_name: null,
    })
    withRecord({ current: declined, history: [agreed, declined] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))

    const items = screen.getByTestId("ai-consent-history").querySelectorAll("li")
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent("Declined Nov 2, 2026")
    expect(items[0]).toHaveTextContent("On the intake form")
    expect(items[1]).toHaveTextContent("Agreed Oct 6, 2026")
    expect(items[1]).toHaveTextContent("Recorded by Dr. Rivera")
  })
})

describe("formatConsentDate", () => {
  it("reads a civil date as that day, wherever the browser is", () => {
    expect(formatConsentDate("2026-01-01")).toBe("Jan 1, 2026")
  })
})
