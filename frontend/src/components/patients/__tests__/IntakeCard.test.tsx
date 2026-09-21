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
const mockAssignments = vi.fn()
const mockArtifacts = vi.fn()
const mockCoverage = vi.fn()
const mockReview = vi.fn()

vi.mock("@/lib/api/patientIntakeSubmissions", () => ({
  listPatientIntakeSubmissions: (...args: unknown[]) => mockList(...args),
}))

// Spread the real module so the panel the card opens keeps the constants it
// reads off it, and only the calls that would reach the network are stubbed.
vi.mock("@/lib/api/intakeReview", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/intakeReview")>()
  return {
    ...actual,
    listIntakeAssignments: (...args: unknown[]) => mockAssignments(...args),
    listIntakeArtifacts: (...args: unknown[]) => mockArtifacts(...args),
    getIntakeReview: (...args: unknown[]) => mockReview(...args),
  }
})

vi.mock("@/lib/api/coverage", () => ({
  fetchCoverage: (...args: unknown[]) => mockCoverage(...args),
}))

vi.mock("@/lib/api/patientDocuments", () => ({
  getPatientDocumentDownloadUrl: vi.fn().mockResolvedValue("https://storage.example/signed"),
}))

const ASSIGNMENT = {
  id: "assignment-1",
  version_id: "version-1",
  packet_name: "Before we meet",
  version: 1,
  status: "submitted",
  assigned_at: "2026-03-01T12:00:00Z",
  submitted_at: "2026-03-14T12:00:00Z",
  receipt_code: "K7M2QP4T",
  progress: { complete: true, missing: [] },
}

/** The same form, read back question by question. */
const REVIEW = {
  ...ASSIGNMENT,
  patient_id: "patient-a",
  items: [
    {
      id: "item-reason",
      key: "reason",
      position: 1,
      item_type: "reason",
      required: true,
      label: "What brings you in?",
      help_text: null,
      config: {},
      value: { text: "Panic before every shift." },
      provenance: "patient",
      superseded_count: 0,
    },
  ],
  signatures: [],
  events: [],
}

const ARTIFACT = {
  id: "artifact-1",
  item_id: "item-records",
  item_label: "Any records from a previous provider",
  side: null,
  document_id: "doc-1",
  filename: "referral.pdf",
  content_type: "application/pdf",
  size_bytes: 2048,
  scan_status: null,
  created_at: "2026-03-14T12:00:00Z",
}

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
    // The ordinary chart: forms were sent and nothing was attached to
    // them. Tests about files say so themselves.
    mockAssignments.mockResolvedValue([])
    mockArtifacts.mockResolvedValue([])
    mockCoverage.mockResolvedValue(null)
    mockReview.mockResolvedValue(REVIEW)
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

  it("shows the files a form collected under what the patient wrote", async () => {
    mockList.mockResolvedValue([submission()])
    mockAssignments.mockResolvedValue([ASSIGNMENT])
    mockArtifacts.mockResolvedValue([ARTIFACT])

    renderCard()

    expect(await screen.findByTestId("intake-artifacts")).toBeInTheDocument()
    expect(screen.getByText("Any records from a previous provider")).toBeInTheDocument()
    expect(mockArtifacts).toHaveBeenCalledWith("patient-a", "assignment-1", undefined)
  })

  it("appears for a form that collected only files", async () => {
    // A practice whose first form asks for a photograph of a card and
    // nothing else still put something on this chart.
    mockList.mockResolvedValue([])
    mockAssignments.mockResolvedValue([ASSIGNMENT])
    mockArtifacts.mockResolvedValue([ARTIFACT])

    renderCard()

    expect(await screen.findByTestId("intake-card")).toBeInTheDocument()
    expect(screen.getByTestId("intake-artifacts")).toBeInTheDocument()
    expect(screen.queryByText(/^Submitted /)).not.toBeInTheDocument()
  })

  it("appears for a form that collected nothing, so it can still be read back", async () => {
    mockList.mockResolvedValue([])
    mockAssignments.mockResolvedValue([ASSIGNMENT])
    mockArtifacts.mockResolvedValue([])

    renderCard()

    expect(await screen.findByTestId("intake-card")).toBeInTheDocument()
    expect(screen.getByTestId("intake-assignments")).toBeInTheDocument()
    expect(screen.queryByTestId("intake-artifacts")).not.toBeInTheDocument()
  })

  it("lists a form by name and by what the server says about it", async () => {
    mockList.mockResolvedValue([])
    mockAssignments.mockResolvedValue([ASSIGNMENT])

    renderCard()

    expect(await screen.findByText("Before we meet v1")).toBeInTheDocument()
    expect(screen.getByText("Handed in.")).toBeInTheDocument()
  })

  it("leaves the review closed, and reads nothing, until asked", async () => {
    mockList.mockResolvedValue([])
    mockAssignments.mockResolvedValue([ASSIGNMENT])

    renderCard()

    await screen.findByTestId("intake-assignment-open-assignment-1")
    expect(screen.queryByTestId("intake-review-panel")).not.toBeInTheDocument()
    expect(mockReview).not.toHaveBeenCalled()
  })

  it("opens the review of a submitted form on the chart", async () => {
    mockList.mockResolvedValue([])
    mockAssignments.mockResolvedValue([ASSIGNMENT])

    renderCard()

    await userEvent.click(
      await screen.findByTestId("intake-assignment-open-assignment-1"),
    )

    expect(await screen.findByTestId("intake-review-panel")).toBeInTheDocument()
    expect(mockReview).toHaveBeenCalledWith("patient-a", "assignment-1", undefined)
    // The clinician's three actions have a screen: reading each answer back,
    // sending named questions back, and accepting the form.
    expect(screen.getByText("Panic before every shift.")).toBeInTheDocument()
    expect(screen.getByTestId("intake-review-corrections")).toBeInTheDocument()
    expect(screen.getByTestId("intake-review-accept")).toBeInTheDocument()
    expect(screen.getByTestId("intake-review-export")).toBeInTheDocument()
  })
})
