// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What was booked automatically from an event's title: listed with client and
 * time, each undoable through the ordinary cancel, and cleared by OK.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import { AutoBookedNotice } from "../AutoBookedNotice"

const api = {
  listAutoBooked: vi.fn(),
  acknowledgeAutoBooked: vi.fn(),
  cancelAppointment: vi.fn(),
}
vi.mock("@/lib/api/outsideSessions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/outsideSessions")>()),
  listAutoBooked: () => api.listAutoBooked(),
  acknowledgeAutoBooked: (ids: string[]) => api.acknowledgeAutoBooked(ids),
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

describe("AutoBookedNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.cancelAppointment.mockResolvedValue({})
    api.acknowledgeAutoBooked.mockResolvedValue({ acknowledged: 2 })
  })

  it("shows nothing when nothing was booked automatically", async () => {
    api.listAutoBooked.mockResolvedValue({ sessions: [] })
    renderWithProviders(<AutoBookedNotice />)

    await waitFor(() => expect(api.listAutoBooked).toHaveBeenCalled())
    expect(screen.queryByTestId("auto-booked")).not.toBeInTheDocument()
  })

  it("lists each session with its client and says why", async () => {
    api.listAutoBooked.mockResolvedValue({ sessions: [JANE, ROBERT] })
    renderWithProviders(<AutoBookedNotice />)

    expect(
      await screen.findByText("Pablo booked 2 sessions from your calendar"),
    ).toBeInTheDocument()
    expect(screen.getByText("Each title had a client’s full name.")).toBeInTheDocument()
    const rows = screen.getAllByTestId("auto-booked-row")
    expect(rows.map((row) => within(row).getByText(/Smith|Jones/).textContent)).toEqual([
      "Jane Smith",
      "Robert Jones",
    ])
  })

  it("undoes one by cancelling its appointment", async () => {
    api.listAutoBooked
      .mockResolvedValueOnce({ sessions: [JANE, ROBERT] })
      .mockResolvedValue({ sessions: [ROBERT] })
    renderWithProviders(<AutoBookedNotice />)

    const [janeRow] = await screen.findAllByTestId("auto-booked-row")
    await userEvent.click(within(janeRow).getByRole("button", { name: /^Undo Jane Smith/ }))

    expect(api.cancelAppointment).toHaveBeenCalledWith("a1")
    await waitFor(() =>
      expect(screen.getByText("Pablo booked 1 session from your calendar")).toBeInTheDocument(),
    )
    expect(screen.queryByText("Jane Smith")).not.toBeInTheDocument()
  })

  it("clears the list with OK, leaving the sessions booked", async () => {
    api.listAutoBooked
      .mockResolvedValueOnce({ sessions: [JANE, ROBERT] })
      .mockResolvedValue({ sessions: [] })
    renderWithProviders(<AutoBookedNotice />)

    await userEvent.click(await screen.findByRole("button", { name: "OK" }))

    expect(api.acknowledgeAutoBooked).toHaveBeenCalledWith(["a1", "a2"])
    expect(api.cancelAppointment).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByTestId("auto-booked")).not.toBeInTheDocument())
  })

  it("hides and shows the list", async () => {
    api.listAutoBooked.mockResolvedValue({ sessions: [JANE] })
    renderWithProviders(<AutoBookedNotice />)

    await userEvent.click(await screen.findByRole("button", { name: "Hide" }))
    expect(screen.queryByTestId("auto-booked-row")).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Show" }))
    expect(screen.getByTestId("auto-booked-row")).toBeInTheDocument()
  })
})
