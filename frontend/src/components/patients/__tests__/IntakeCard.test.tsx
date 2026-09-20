// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * IntakeCard tests.
 *
 * The API module is mocked rather than the hook, so the query key, the
 * `enabled` guard and the no-retry posture are all exercised on the way
 * through. What the card must get right: show what the patient wrote, raise
 * a flag when they said the chart has them wrong, render no score of any
 * kind, and disappear entirely when there is nothing to show.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IntakeCard } from "../IntakeCard"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { PatientIntakeSubmission } from "@/types/patientIntakeSubmissions"

const mockList = vi.fn()

vi.mock("@/lib/api/patientIntakeSubmissions", () => ({
  listPatientIntakeSubmissions: (...args: unknown[]) => mockList(...args),
}))

const NEWEST = "2026-03-14T12:00:00Z"
const OLDEST = "2026-01-09T12:00:00Z"

function submission(
  overrides: Partial<PatientIntakeSubmission> = {},
): PatientIntakeSubmission {
  return {
    id: "intake-1",
    submitted_at: NEWEST,
    name_confirmed: true,
    dob_confirmed: true,
    corrections: null,
    reason_text: "Panic at work for about two months.",
    ...overrides,
  }
}

function renderCard() {
  return renderWithProviders(<IntakeCard patientId="patient-a" />)
}

describe("IntakeCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("shows the reason and the submitted date of the latest submission", async () => {
    mockList.mockResolvedValue([submission()])

    renderCard()

    expect(
      await screen.findByText("Panic at work for about two months."),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        `Submitted ${new Date(NEWEST).toLocaleDateString()}`,
      ),
    ).toBeInTheDocument()
  })

  it("reads the patient's own submissions", async () => {
    mockList.mockResolvedValue([submission()])

    renderCard()

    await screen.findByTestId("intake-card")
    expect(mockList).toHaveBeenCalledWith("patient-a", undefined)
  })

  it("flags a correction the patient wrote", async () => {
    mockList.mockResolvedValue([
      submission({ corrections: "My last name is spelled Lovelace-Byron." }),
    ])

    renderCard()

    expect(await screen.findByRole("note")).toHaveTextContent(
      "My last name is spelled Lovelace-Byron.",
    )
  })

  it("flags an unconfirmed name even with nothing written", async () => {
    mockList.mockResolvedValue([submission({ name_confirmed: false })])

    renderCard()

    expect(await screen.findByRole("note")).toHaveTextContent(
      "Did not confirm the name on file.",
    )
  })

  it("flags an unconfirmed date of birth even with nothing written", async () => {
    mockList.mockResolvedValue([submission({ dob_confirmed: false })])

    renderCard()

    expect(await screen.findByRole("note")).toHaveTextContent(
      "Did not confirm the date of birth on file.",
    )
  })

  it("raises no flag when everything was confirmed", async () => {
    mockList.mockResolvedValue([submission()])

    renderCard()

    await screen.findByTestId("intake-card")
    expect(screen.queryByRole("note")).not.toBeInTheDocument()
  })

  it("renders nothing when the patient has no submissions", async () => {
    mockList.mockResolvedValue([])

    const { container } = renderCard()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    expect(screen.queryByTestId("intake-card")).not.toBeInTheDocument()
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when the read fails", async () => {
    mockList.mockRejectedValue(new Error("boom"))

    const { container } = renderCard()

    await waitFor(() => expect(mockList).toHaveBeenCalled())
    expect(screen.queryByTestId("intake-card")).not.toBeInTheDocument()
    expect(container).toBeEmptyDOMElement()
  })

  it("carries no score and no severity band", async () => {
    mockList.mockResolvedValue([
      submission({ corrections: "Date of birth is 1990-03-14." }),
    ])

    renderCard()

    await screen.findByTestId("intake-card")
    const rendered = screen.getByTestId("intake-card").textContent ?? ""
    for (const forbidden of [
      "PHQ",
      "GAD",
      "Score",
      "score",
      "Minimal",
      "Mild",
      "Moderate",
      "Severe",
    ]) {
      expect(rendered).not.toContain(forbidden)
    }
  })

  it("keeps earlier submissions collapsed until asked", async () => {
    mockList.mockResolvedValue([
      submission({ id: "newer", reason_text: "The recent one." }),
      submission({
        id: "older",
        submitted_at: OLDEST,
        reason_text: "The first one.",
      }),
    ])

    renderCard()

    expect(await screen.findByText("The recent one.")).toBeInTheDocument()
    expect(screen.queryByText("The first one.")).not.toBeInTheDocument()

    await userEvent.click(
      screen.getByRole("button", { name: /earlier submissions \(1\)/i }),
    )

    expect(screen.getByText("The first one.")).toBeInTheDocument()
    expect(
      screen.getByText(`Submitted ${new Date(OLDEST).toLocaleDateString()}`),
    ).toBeInTheDocument()
  })

  it("offers no expander when there is only one submission", async () => {
    mockList.mockResolvedValue([submission()])

    renderCard()

    await screen.findByTestId("intake-card")
    expect(
      screen.queryByRole("button", { name: /earlier submissions/i }),
    ).not.toBeInTheDocument()
  })
})
