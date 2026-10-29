// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The waitlist in settings: entries top first with what each asked for; a
 * move sends the whole list in its new order, and a 409 reads the list again
 * rather than guessing; pause, the automatic-offer switch (stored inverted)
 * and remove each send one change; an empty list says how to add someone.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { ApiError } from "@/lib/api/client"
import type { WaitlistEntry } from "@/types/waitlist"
import { WaitlistList, movedOrder } from "../WaitlistList"
import { entrySummary } from "../waitlistCopy"

let entries: WaitlistEntry[]
const refetch = vi.fn()
const reorder = vi.fn()
const update = vi.fn()
const remove = vi.fn()
const showToast = vi.fn()

vi.mock("@/hooks/useWaitlist", () => ({
  useWaitlist: () => ({ data: { entries }, isPending: false, error: null, refetch }),
  useReorderWaitlist: () => ({ mutateAsync: reorder, isPending: false }),
  useUpdateWaitlistEntry: () => ({ mutateAsync: update, isPending: false }),
  useRemoveWaitlistEntry: () => ({ mutateAsync: remove, isPending: false }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast }),
}))

function entry(overrides: Partial<WaitlistEntry>): WaitlistEntry {
  return {
    id: "w1",
    patient_id: "p1",
    patient_name: "Rosa Diaz",
    days: ["mon"],
    times: ["morning"],
    modality: "in_person",
    session_type: null,
    position: 1,
    paused: false,
    no_auto_offer: false,
    created_at: "2026-10-20T15:00:00Z",
    updated_at: "2026-10-20T15:00:00Z",
    ...overrides,
  }
}

const ROSA = entry({})
const OMAR = entry({
  id: "w2",
  patient_id: "p2",
  patient_name: "Omar Haddad",
  days: ["mon", "tue", "wed", "thu", "fri"],
  times: ["morning", "afternoon", "evening"],
  modality: "either",
  session_type: "couples",
  position: 2,
  paused: true,
  no_auto_offer: true,
})
const LENA = entry({ id: "w3", patient_id: "p3", patient_name: "Lena Park", position: 3 })

function rows() {
  return screen.getAllByTestId("waitlist-entry")
}

describe("WaitlistList", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    entries = [ROSA, OMAR, LENA]
  })

  it("says how to add someone when the list is empty", () => {
    entries = []
    render(<WaitlistList />)
    expect(screen.getByTestId("waitlist-empty")).toHaveTextContent(
      "No one is on your waitlist. Add a client from their page.",
    )
  })

  it("lists entries top first with what each asked for", () => {
    render(<WaitlistList />)

    expect(rows().map((r) => r.getAttribute("data-entry-id"))).toEqual(["w1", "w2", "w3"])
    const omar = rows()[1]
    expect(within(omar).getByTestId("waitlist-entry-summary")).toHaveTextContent(
      "Weekdays · Any time of day · In person or video · Couples",
    )
    expect(omar).toHaveTextContent("Paused")
    expect(within(omar).getByRole("link", { name: "Omar Haddad" })).toHaveAttribute(
      "href",
      "/dashboard/patients/p2",
    )
  })

  it("cannot move the top entry up or the bottom one down", () => {
    render(<WaitlistList />)
    expect(screen.getByRole("button", { name: "Move Rosa Diaz up" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Move Lena Park down" })).toBeDisabled()
  })

  it("sends the whole list in its new order", async () => {
    reorder.mockResolvedValue({ entries })
    render(<WaitlistList />)

    fireEvent.click(screen.getByRole("button", { name: "Move Lena Park up" }))

    await waitFor(() => expect(reorder).toHaveBeenCalledWith(["w1", "w3", "w2"]))
  })

  it("reads the list again when it changed underneath", async () => {
    reorder.mockRejectedValue(new ApiError("CONFLICT", "The waitlist changed.", undefined, 409))
    render(<WaitlistList />)

    fireEvent.click(screen.getByRole("button", { name: "Move Rosa Diaz down" }))

    await waitFor(() => expect(refetch).toHaveBeenCalled())
    expect(showToast).toHaveBeenCalledWith(
      "The waitlist changed. Try again with the latest order.",
      "error",
    )
  })

  it("pauses and resumes", async () => {
    render(<WaitlistList />)

    fireEvent.click(within(rows()[0]).getByRole("button", { name: "Pause" }))
    fireEvent.click(within(rows()[1]).getByRole("button", { name: "Resume" }))

    await waitFor(() => expect(update).toHaveBeenCalledTimes(2))
    expect(update).toHaveBeenCalledWith({ entryId: "w1", data: { paused: true } })
    expect(update).toHaveBeenCalledWith({ entryId: "w2", data: { paused: false } })
  })

  it("shows automatic offers on unless switched off, and stores the switch inverted", async () => {
    render(<WaitlistList />)

    const rosa = screen.getByRole("switch", { name: "Offer times automatically for Rosa Diaz" })
    const omar = screen.getByRole("switch", { name: "Offer times automatically for Omar Haddad" })
    expect(rosa).toHaveAttribute("aria-checked", "true")
    expect(omar).toHaveAttribute("aria-checked", "false")

    fireEvent.click(rosa)
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith({ entryId: "w1", data: { no_auto_offer: true } }),
    )
  })

  it("removes an entry", async () => {
    render(<WaitlistList />)
    fireEvent.click(within(rows()[2]).getByRole("button", { name: "Remove" }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith("w3"))
  })
})

describe("movedOrder", () => {
  it("swaps an entry with its neighbour", () => {
    expect(movedOrder([ROSA, OMAR, LENA], 0, 1)).toEqual(["w2", "w1", "w3"])
    expect(movedOrder([ROSA, OMAR, LENA], 2, -1)).toEqual(["w1", "w3", "w2"])
  })
})

describe("entrySummary", () => {
  it("names days and times in week and day order", () => {
    expect(
      entrySummary({
        days: ["sat", "mon"],
        times: ["evening", "morning"],
        modality: "telehealth",
        session_type: "family",
      }),
    ).toBe("Mon, Sat · Morning, evening · Video · Family")
  })

  it("says any day when every day is chosen", () => {
    expect(
      entrySummary({
        days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        times: ["afternoon"],
        modality: "in_person",
        session_type: null,
      }),
    ).toBe("Any day · Afternoon · In person")
  })
})
