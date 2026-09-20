// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The module's outer surface: what a patient was asked for, and getting
 * into one of them.
 *
 * What a row says about a form is the server's `progress`, which is why the
 * fixtures set it rather than the rows implying it — a test that counted
 * unanswered items here would be asserting the thing the module is designed
 * never to do.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import { PatientIntakeError } from "@/lib/api/patientIntake"
import { PortalForms } from "../PortalForms"
import { ASSIGNMENT, INTAKE_FORM, assignmentDetail } from "./formFixtures"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return {
    ...actual,
    listAssignments: vi.fn(),
    fetchIntakeForm: vi.fn(),
    fetchAssignment: vi.fn(),
    saveAnswer: vi.fn(),
    submitAssignment: vi.fn(),
  }
})

const TOKEN = "portal-session-token"

function renderModule() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PortalFormsWrapper"
  return render(<PortalForms sessionToken={TOKEN} />, { wrapper: Wrapper })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.fetchIntakeForm).mockResolvedValue(INTAKE_FORM)
  vi.mocked(api.listAssignments).mockResolvedValue([ASSIGNMENT])
  vi.mocked(api.fetchAssignment).mockResolvedValue(assignmentDetail())
})

describe("PortalForms", () => {
  it("lists what the practice asked for, and what is outstanding", async () => {
    renderModule()

    expect(await screen.findByTestId("forms-list")).toBeInTheDocument()
    expect(screen.getByText("Intake")).toBeInTheDocument()
    expect(screen.getByTestId("forms-list-state")).toHaveTextContent("4 questions left")
  })

  it("says a form is ready to send only when the server says it is", async () => {
    vi.mocked(api.listAssignments).mockResolvedValue([
      { ...ASSIGNMENT, status: "in_progress", progress: { complete: true, missing: [] } },
    ])

    renderModule()

    expect(await screen.findByTestId("forms-list-state")).toHaveTextContent("Ready to send")
  })

  it("offers no way in once a form has been handed in", async () => {
    vi.mocked(api.listAssignments).mockResolvedValue([
      {
        ...ASSIGNMENT,
        status: "submitted",
        submitted_at: "2026-09-20T09:30:00Z",
        receipt_code: "K3MTQ7BX",
        progress: { complete: true, missing: [] },
      },
    ])

    renderModule()

    expect(await screen.findByTestId("forms-list-state")).toHaveTextContent("Sent")
    expect(screen.queryByTestId("forms-list-open")).not.toBeInTheDocument()
  })

  it("says so plainly when nothing has been asked for", async () => {
    vi.mocked(api.listAssignments).mockResolvedValue([])

    renderModule()

    expect(await screen.findByTestId("forms-list-empty")).toHaveTextContent(
      "Nothing to fill in right now.",
    )
  })

  it("opens a form when the patient asks for it", async () => {
    const user = userEvent.setup()
    renderModule()

    await user.click(await screen.findByTestId("forms-list-open"))

    expect(await screen.findByTestId("forms-item-screen")).toBeInTheDocument()
    expect(api.fetchAssignment).toHaveBeenCalledWith(TOKEN, ASSIGNMENT.id)
  })

  it("sends a dead session to the guidance about getting a new link", async () => {
    vi.mocked(api.listAssignments).mockRejectedValue(
      new PatientIntakeError("expired", "Session expired"),
    )

    renderModule()

    expect(await screen.findByTestId("forms-expired")).toBeInTheDocument()
  })

  it("offers a retry when the list will not load", async () => {
    vi.mocked(api.listAssignments).mockRejectedValue(
      new PatientIntakeError("unavailable", "Could not reach the server"),
    )

    renderModule()

    expect(await screen.findByTestId("forms-load-failed")).toBeInTheDocument()
  })

  it("carries on when the engine's own wording does not arrive", async () => {
    // A deployment that does not answer the form route leaves the questions
    // that need its wording saying so; the list still works.
    vi.mocked(api.fetchIntakeForm).mockRejectedValue(
      new PatientIntakeError("unavailable", "Could not reach the server"),
    )

    renderModule()

    expect(await screen.findByTestId("forms-list")).toBeInTheDocument()
  })
})
