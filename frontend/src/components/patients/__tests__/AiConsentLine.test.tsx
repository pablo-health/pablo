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

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AiConsentLine, formatConsentDate } from "../AiConsentLine"
import { todayCivilDate } from "@/lib/dates"
import type { AiConsentEntry, AiConsentRecord } from "@/types/aiConsent"

const mockUseAiConsent = vi.fn()
const mockMutateAsync = vi.fn()

// The clinician's timezone preference. Tests run with the browser in UTC
// (vitest.config.ts), so a zone west of it puts the clinician a day behind the
// browser every evening.
let mockTimeZone = "America/New_York"
vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => mockTimeZone,
}))

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

function clinicianToday(): string {
  return todayCivilDate(mockTimeZone)
}

describe("AiConsentLine", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockTimeZone = "America/New_York"
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
    expect(screen.getByLabelText("Date")).toHaveValue(clinicianToday())
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()

    await user.click(screen.getByLabelText("Client agreed"))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "p1",
      data: { decision: "consented", effective_on: clinicianToday(), consented_by: "client" },
    })
  })

  it("records a parent's answer over telehealth with where the client said they were", async () => {
    const user = userEvent.setup()
    withRecord({ current: null, history: [] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    await user.click(screen.getByLabelText("Client agreed"))
    await user.selectOptions(screen.getByLabelText("Answered by"), "parent")
    expect(screen.queryByLabelText("Where the client said they were")).not.toBeInTheDocument()
    await user.click(screen.getByLabelText("Telehealth"))
    await user.type(screen.getByLabelText("Where the client said they were"), " At home ")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "p1",
      data: {
        decision: "consented",
        effective_on: clinicianToday(),
        modality: "telehealth",
        client_stated_location: "At home",
        consented_by: "parent",
      },
    })
  })

  it("does not send a place for an answer given in person", async () => {
    const user = userEvent.setup()
    withRecord({ current: null, history: [] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    await user.click(screen.getByLabelText("Client agreed"))
    await user.click(screen.getByLabelText("Telehealth"))
    await user.type(screen.getByLabelText("Where the client said they were"), "At home")
    await user.click(screen.getByLabelText("In person"))
    await user.click(screen.getByRole("button", { name: "Save" }))

    const sent = mockMutateAsync.mock.calls[0][0].data
    expect(sent.modality).toBe("in_person")
    expect(sent.client_stated_location).toBeUndefined()
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
      data: { decision: "declined", effective_on: "2026-09-30", consented_by: "client" },
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
    expect(items[0]).toHaveTextContent("On their intake packet")
    expect(items[1]).toHaveTextContent("Agreed Oct 6, 2026")
    expect(items[1]).toHaveTextContent("Recorded by Dr. Rivera")
  })

  it("shows how each answer was given, and nothing extra for one without it", async () => {
    const user = userEvent.setup()
    const older = entry({ id: "a", effective_on: "2026-09-01" })
    const latest = entry({
      id: "b",
      effective_on: "2026-10-06",
      modality: "telehealth",
      client_stated_location: "At home",
      consented_by: "guardian",
    })
    withRecord({ current: latest, history: [older, latest] })
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))

    const items = screen.getByTestId("ai-consent-history").querySelectorAll("li")
    expect(items[0]).toHaveTextContent("Agreed Oct 6, 2026 · Telehealth · At home · Guardian")
    expect(items[1]).toHaveTextContent(/^Agreed Sep 1, 2026Recorded by Dr\. Rivera$/)
  })
})

describe("AiConsentLine dates on the clinician's calendar", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockTimeZone = "America/New_York"
    mockMutateAsync.mockResolvedValue({ current: null, history: [] })
    withRecord({ current: null, history: [] })
    vi.useFakeTimers({ toFake: ["Date"] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("defaults to the clinician's today in the evening, when the browser is already on tomorrow", async () => {
    // 01:15 UTC on Nov 17 is 20:15 on Nov 16 in New York.
    vi.setSystemTime(new Date("2026-11-17T01:15:00Z"))
    const user = userEvent.setup()
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    const date = screen.getByLabelText("Date")
    expect(date).toHaveValue("2026-11-16")
    expect(date).toHaveAttribute("max", "2026-11-16")

    await user.click(screen.getByLabelText("Client agreed"))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockMutateAsync).toHaveBeenCalledWith({
      patientId: "p1",
      data: { decision: "consented", effective_on: "2026-11-16", consented_by: "client" },
    })
  })

  it("lets a clinician east of the browser pick their own today", async () => {
    // 20:00 UTC on Nov 16 is 05:00 on Nov 17 in Tokyo.
    vi.setSystemTime(new Date("2026-11-16T20:00:00Z"))
    mockTimeZone = "Asia/Tokyo"
    const user = userEvent.setup()
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    const date = screen.getByLabelText("Date")
    expect(date).toHaveValue("2026-11-17")
    expect(date).toHaveAttribute("max", "2026-11-17")
  })

  it("follows the preference when it arrives after the chart has mounted", async () => {
    vi.setSystemTime(new Date("2026-11-17T01:15:00Z"))
    // Before preferences load, the zone falls back to the browser's (UTC).
    mockTimeZone = "UTC"
    const user = userEvent.setup()
    const { rerender } = render(<AiConsentLine patientId="p1" />)
    mockTimeZone = "America/New_York"
    rerender(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    expect(screen.getByLabelText("Date")).toHaveValue("2026-11-16")
  })

  it("starts from the clinician's today again after a chosen day is cancelled", async () => {
    vi.setSystemTime(new Date("2026-11-17T01:15:00Z"))
    const user = userEvent.setup()
    render(<AiConsentLine patientId="p1" />)

    await user.click(screen.getByTestId("ai-consent-line"))
    const date = screen.getByLabelText("Date")
    await user.clear(date)
    await user.type(date, "2026-11-02")
    expect(date).toHaveValue("2026-11-02")
    await user.keyboard("{Escape}")

    await user.click(screen.getByTestId("ai-consent-line"))
    expect(screen.getByLabelText("Date")).toHaveValue("2026-11-16")
  })
})

describe("formatConsentDate", () => {
  it("reads a civil date as that day, wherever the browser is", () => {
    expect(formatConsentDate("2026-01-01")).toBe("Jan 1, 2026")
  })
})
