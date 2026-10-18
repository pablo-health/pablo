// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalHomework over a mocked client: open work first with a "Mark done"
 * that opens an optional note under the crisis line; done work shows its
 * date and note and can be marked not done; an empty list and a failed load
 * say so plainly; and the home tile counts what is left.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { PatientHomework } from "@/lib/api/patientHomework"
import * as api from "@/lib/api/patientHomework"
import { CRISIS_LINE } from "../../messaging/ExpectationNotice"
import { HomeworkSummary } from "../HomeworkSummary"
import { PortalHomework } from "../PortalHomework"
import { summaryLine } from "../homeworkCopy"

vi.mock("@/lib/api/patientHomework", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientHomework")>()
  return {
    ...actual,
    listHomework: vi.fn(),
    markHomeworkDone: vi.fn(),
    markHomeworkNotDone: vi.fn(),
  }
})

const TOKEN = "session-token"

const open: PatientHomework = {
  id: "h-open",
  text: "Keep a sleep diary each night",
  status: "assigned",
  assigned_at: "2026-10-06T15:00:00Z",
  completed_at: null,
  client_note: null,
}

const done: PatientHomework = {
  id: "h-done",
  text: "Walk after dinner",
  status: "done",
  assigned_at: "2026-10-01T15:00:00Z",
  completed_at: "2026-10-04T15:00:00Z",
  client_note: "Three evenings",
}

function renderWith(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listHomework).mockResolvedValue({ data: [open, done], total: 2 })
  vi.mocked(api.markHomeworkDone).mockResolvedValue({ ...open, status: "done" })
  vi.mocked(api.markHomeworkNotDone).mockResolvedValue({ ...done, status: "assigned" })
})

describe("PortalHomework", () => {
  it("marks open work done with an optional note, under the crisis line", async () => {
    renderWith(<PortalHomework sessionToken={TOKEN} />)
    const row = await screen.findByTestId("portal-homework-item-h-open")
    expect(within(row).getByText("Assigned Oct 6, 2026")).toBeInTheDocument()

    await userEvent.click(within(row).getByRole("button", { name: "Mark done" }))
    expect(within(row).getByText(CRISIS_LINE)).toBeInTheDocument()
    await userEvent.type(within(row).getByLabelText(/Anything to tell your clinician/), "  Five nights ")
    await userEvent.click(screen.getByTestId("portal-homework-confirm-h-open"))

    await waitFor(() =>
      expect(api.markHomeworkDone).toHaveBeenCalledWith(TOKEN, "h-open", "Five nights"),
    )
  })

  it("sends no note when none was written", async () => {
    renderWith(<PortalHomework sessionToken={TOKEN} />)
    const row = await screen.findByTestId("portal-homework-item-h-open")
    await userEvent.click(within(row).getByRole("button", { name: "Mark done" }))
    await userEvent.click(screen.getByTestId("portal-homework-confirm-h-open"))
    await waitFor(() => expect(api.markHomeworkDone).toHaveBeenCalledWith(TOKEN, "h-open", null))
  })

  it("shows done work with its date and note, and can mark it not done", async () => {
    renderWith(<PortalHomework sessionToken={TOKEN} />)
    const row = await screen.findByTestId("portal-homework-item-h-done")
    expect(within(row).getByText("Done Oct 4, 2026")).toBeInTheDocument()
    expect(within(row).getByText("“Three evenings”")).toBeInTheDocument()
    await userEvent.click(within(row).getByRole("button", { name: "Not done yet" }))
    await waitFor(() => expect(api.markHomeworkNotDone).toHaveBeenCalledWith(TOKEN, "h-done"))
  })

  it("says when nothing is assigned, and when the list cannot load", async () => {
    vi.mocked(api.listHomework).mockResolvedValueOnce({ data: [], total: 0 })
    const { unmount } = renderWith(<PortalHomework sessionToken={TOKEN} />)
    expect(await screen.findByText("Nothing assigned right now.")).toBeInTheDocument()
    unmount()

    vi.mocked(api.listHomework).mockRejectedValueOnce(new api.PatientHomeworkError(500))
    renderWith(<PortalHomework sessionToken={TOKEN} />)
    expect(await screen.findByTestId("portal-homework-load-error")).toBeInTheDocument()
  })
})

describe("HomeworkSummary", () => {
  it("counts what is left to do", async () => {
    renderWith(<HomeworkSummary sessionToken={TOKEN} />)
    expect(await screen.findByText("1 to do")).toBeInTheDocument()
  })

  it("reads all done and nothing assigned", () => {
    expect(summaryLine(0, 2)).toBe("All done")
    expect(summaryLine(0, 0)).toBe("Nothing assigned")
    expect(summaryLine(3, 1)).toBe("3 to do")
  })
})
