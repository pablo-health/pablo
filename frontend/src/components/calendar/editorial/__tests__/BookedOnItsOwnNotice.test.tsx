// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What Pablo booked on its own from an event's title: listed with client and
 * time, each undoable through the ordinary cancel, and cleared by OK.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import { BookedOnItsOwnNotice } from "../BookedOnItsOwnNotice"

const api = {
  listBookedOnItsOwn: vi.fn(),
  markBookedOnItsOwnSeen: vi.fn(),
  cancelAppointment: vi.fn(),
}
vi.mock("@/lib/api/outsideSessions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/outsideSessions")>()),
  listBookedOnItsOwn: () => api.listBookedOnItsOwn(),
  markBookedOnItsOwnSeen: (ids: string[]) => api.markBookedOnItsOwnSeen(ids),
}))
vi.mock("@/lib/api/scheduling", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/scheduling")>()),
  cancelAppointment: (id: string) => api.cancelAppointment(id),
}))

const JANE = {
  appointment_id: "a1",
  patient_id: "p1",
  client_name: "Jane Smith",
  start_at: "2026-10-05T16:00:00Z",
  end_at: "2026-10-05T16:50:00Z",
  source: "google_calendar",
}
const ROBERT = { ...JANE, appointment_id: "a2", patient_id: "p2", client_name: "Robert Jones" }

describe("BookedOnItsOwnNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.cancelAppointment.mockResolvedValue({})
    api.markBookedOnItsOwnSeen.mockResolvedValue({ seen: 2 })
  })

  it("shows nothing when Pablo booked nothing on its own", async () => {
    api.listBookedOnItsOwn.mockResolvedValue({ sessions: [] })
    renderWithProviders(<BookedOnItsOwnNotice />)

    await waitFor(() => expect(api.listBookedOnItsOwn).toHaveBeenCalled())
    expect(screen.queryByTestId("booked-on-its-own")).not.toBeInTheDocument()
  })

  it("lists each session with its client and says why", async () => {
    api.listBookedOnItsOwn.mockResolvedValue({ sessions: [JANE, ROBERT] })
    renderWithProviders(<BookedOnItsOwnNotice />)

    expect(
      await screen.findByText("Pablo booked 2 sessions from your calendar"),
    ).toBeInTheDocument()
    expect(screen.getByText("Each title had a client’s full name.")).toBeInTheDocument()
    const rows = screen.getAllByTestId("booked-on-its-own-row")
    expect(rows.map((row) => within(row).getByText(/Smith|Jones/).textContent)).toEqual([
      "Jane Smith",
      "Robert Jones",
    ])
  })

  it("undoes one by cancelling its appointment", async () => {
    api.listBookedOnItsOwn
      .mockResolvedValueOnce({ sessions: [JANE, ROBERT] })
      .mockResolvedValue({ sessions: [ROBERT] })
    renderWithProviders(<BookedOnItsOwnNotice />)

    const [janeRow] = await screen.findAllByTestId("booked-on-its-own-row")
    await userEvent.click(within(janeRow).getByRole("button", { name: /^Undo Jane Smith/ }))

    expect(api.cancelAppointment).toHaveBeenCalledWith("a1")
    await waitFor(() =>
      expect(screen.getByText("Pablo booked 1 session from your calendar")).toBeInTheDocument(),
    )
    expect(screen.queryByText("Jane Smith")).not.toBeInTheDocument()
  })

  it("clears the list with OK, leaving the sessions booked", async () => {
    api.listBookedOnItsOwn
      .mockResolvedValueOnce({ sessions: [JANE, ROBERT] })
      .mockResolvedValue({ sessions: [] })
    renderWithProviders(<BookedOnItsOwnNotice />)

    await userEvent.click(await screen.findByRole("button", { name: "OK" }))

    expect(api.markBookedOnItsOwnSeen).toHaveBeenCalledWith(["a1", "a2"])
    expect(api.cancelAppointment).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByTestId("booked-on-its-own")).not.toBeInTheDocument())
  })

  it("hides and shows the list", async () => {
    api.listBookedOnItsOwn.mockResolvedValue({ sessions: [JANE] })
    renderWithProviders(<BookedOnItsOwnNotice />)

    await userEvent.click(await screen.findByRole("button", { name: "Hide" }))
    expect(screen.queryByTestId("booked-on-its-own-row")).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Show" }))
    expect(screen.getByTestId("booked-on-its-own-row")).toBeInTheDocument()
  })
})
