// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * FormsSummary: the forms tile's line on the portal home screen.
 *
 * What counts as "to complete" is the server's status on each row — the same
 * statuses the list offers a way back into — never a count this screen made.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import { FormsSummary, formsSummaryLine } from "../FormsSummary"
import { PortalForms } from "../PortalForms"
import { ASSIGNMENT, INTAKE_FORM } from "./formFixtures"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return { ...actual, listAssignments: vi.fn(), fetchIntakeForm: vi.fn() }
})

const TOKEN = "portal-session-token"

function renderWithClient(ui: React.ReactNode, client = new QueryClient()) {
  const utils = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
  return { ...utils, client }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.fetchIntakeForm).mockResolvedValue(INTAKE_FORM)
})

describe("formsSummaryLine", () => {
  it.each([
    [0, "Nothing to fill in"],
    [1, "1 form to complete"],
    [2, "2 forms to complete"],
  ])("%i open reads %s", (open, line) => {
    expect(formsSummaryLine(open)).toBe(line)
  })
})

describe("FormsSummary", () => {
  it("counts only the forms a patient can still write to", async () => {
    vi.mocked(api.listAssignments).mockResolvedValue([
      { ...ASSIGNMENT, id: "a", status: "assigned" },
      { ...ASSIGNMENT, id: "b", status: "in_progress" },
      { ...ASSIGNMENT, id: "c", status: "submitted" },
    ])

    renderWithClient(<FormsSummary sessionToken={TOKEN} />)

    expect(await screen.findByText("2 forms to complete")).toBeTruthy()
  })

  it("says there is nothing to fill in when every form is in", async () => {
    vi.mocked(api.listAssignments).mockResolvedValue([{ ...ASSIGNMENT, status: "submitted" }])

    renderWithClient(<FormsSummary sessionToken={TOKEN} />)

    expect(await screen.findByText("Nothing to fill in")).toBeTruthy()
  })

  it("renders nothing when the list fails to load", async () => {
    vi.mocked(api.listAssignments).mockRejectedValue(new Error("boom"))

    const { container } = renderWithClient(<FormsSummary sessionToken={TOKEN} />)

    await waitFor(() => expect(api.listAssignments).toHaveBeenCalled())
    expect(container.textContent).toBe("")
  })

  it("shares its cache with the forms section", async () => {
    vi.mocked(api.listAssignments).mockResolvedValue([ASSIGNMENT])
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000 } } })

    const { unmount } = renderWithClient(<FormsSummary sessionToken={TOKEN} />, client)
    await screen.findByText("1 form to complete")
    unmount()
    renderWithClient(<PortalForms sessionToken={TOKEN} />, client)
    await screen.findByTestId("forms-list")

    expect(api.listAssignments).toHaveBeenCalledTimes(1)
  })
})
