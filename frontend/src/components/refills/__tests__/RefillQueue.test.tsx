// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RefillQueue Component Tests
 *
 * The states the queue has to get right: loading, a load failure with a
 * retry, nothing waiting, and a populated list whose rows link to the chart.
 * Then the decision: two clicks (pick, confirm), the note trimmed or sent as
 * null, cancel putting the row back, and the two ways it can fail — answered
 * elsewhere (409), and anything else.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { RefillQueue } from "../RefillQueue"
import type { ClinicianRefillRequest, RefillQueueView } from "@/lib/api/refillRequests"

const useRefillQueue = vi.hoisted(() => vi.fn())
const mutateAsync = vi.hoisted(() => vi.fn())
const showToast = vi.hoisted(() => vi.fn())
const isAlreadyAnswered = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useRefillRequests", () => ({
  useRefillQueue: (view: RefillQueueView) => useRefillQueue(view),
  useDecideRefill: () => ({ mutateAsync, isPending: false }),
  isAlreadyAnswered: (error: unknown) => isAlreadyAnswered(error),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast }),
}))

vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
  formatInUserTimeZone: (
    date: Date | string,
    timeZone: string,
    options: Intl.DateTimeFormatOptions,
  ) => new Date(date).toLocaleString("en-US", { ...options, timeZone }),
}))

function request(overrides: Partial<ClinicianRefillRequest> = {}): ClinicianRefillRequest {
  return {
    id: "req-1",
    patient_id: "patient-1",
    patient_name: "Ada Lovelace",
    medication_id: "med-1",
    medication_text: "Sertraline 50 mg",
    pharmacy_text: "Main St pharmacy",
    patient_note: "Running out Friday",
    status: "requested",
    created_at: "2026-09-27T14:00:00Z",
    decided_at: null,
    decided_by_user_id: null,
    prescriber_note: null,
    ...overrides,
  }
}

function queues(
  pending: Partial<ReturnType<typeof useRefillQueue>>,
  recent: ClinicianRefillRequest[] = [],
) {
  useRefillQueue.mockImplementation((view: RefillQueueView) =>
    view === "pending"
      ? { data: undefined, isLoading: false, isError: false, refetch: vi.fn(), ...pending }
      : { data: { data: recent, total: recent.length }, isLoading: false, isError: false },
  )
}

beforeEach(() => {
  useRefillQueue.mockReset()
  mutateAsync.mockReset()
  showToast.mockReset()
  isAlreadyAnswered.mockReset()
})

describe("RefillQueue", () => {
  it("shows a loading state", () => {
    queues({ isLoading: true })
    render(<RefillQueue />)
    expect(screen.getByTestId("refill-queue-loading")).toBeInTheDocument()
  })

  it("offers a retry when the queue fails to load", async () => {
    const refetch = vi.fn()
    queues({ isError: true, refetch })
    render(<RefillQueue />)

    expect(screen.getByRole("alert")).toHaveTextContent("didn’t load")
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it("says so when nothing is waiting", () => {
    queues({ data: { data: [], total: 0 } })
    render(<RefillQueue />)
    expect(screen.getByTestId("refill-queue-empty")).toHaveTextContent("No refill requests waiting")
  })

  it("lists each request with the patient linked to their chart", () => {
    queues({ data: { data: [request()], total: 1 } })
    render(<RefillQueue />)

    const row = screen.getByTestId("refill-row-req-1")
    expect(row).toHaveTextContent("Sertraline 50 mg")
    expect(row).toHaveTextContent("Main St pharmacy")
    expect(row).toHaveTextContent("Running out Friday")
    expect(screen.getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute(
      "href",
      "/dashboard/patients/patient-1",
    )
  })

  it("records a decision in two clicks, with the note trimmed", async () => {
    mutateAsync.mockResolvedValue(request({ status: "approved" }))
    queues({ data: { data: [request()], total: 1 } })
    render(<RefillQueue />)

    await userEvent.click(screen.getByTestId("refill-decide-approved"))
    expect(mutateAsync).not.toHaveBeenCalled()
    await userEvent.type(screen.getByTestId("refill-note"), "  sent to CVS  ")
    await userEvent.click(screen.getByTestId("refill-confirm-submit"))

    expect(mutateAsync).toHaveBeenCalledWith({
      requestId: "req-1",
      status: "approved",
      prescriberNote: "sent to CVS",
    })
  })

  it("sends no note when none was written", async () => {
    mutateAsync.mockResolvedValue(request({ status: "declined" }))
    queues({ data: { data: [request()], total: 1 } })
    render(<RefillQueue />)

    await userEvent.click(screen.getByTestId("refill-decide-declined"))
    await userEvent.click(screen.getByTestId("refill-confirm-submit"))

    expect(mutateAsync).toHaveBeenCalledWith({
      requestId: "req-1",
      status: "declined",
      prescriberNote: null,
    })
  })

  it("puts the choices back on cancel", async () => {
    queues({ data: { data: [request()], total: 1 } })
    render(<RefillQueue />)

    await userEvent.click(screen.getByTestId("refill-decide-needs_visit"))
    expect(screen.getByTestId("refill-confirm")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }))

    expect(screen.queryByTestId("refill-confirm")).not.toBeInTheDocument()
    expect(screen.getByTestId("refill-decide-needs_visit")).toBeInTheDocument()
    expect(mutateAsync).not.toHaveBeenCalled()
  })

  it("says so when a colleague answered first", async () => {
    mutateAsync.mockRejectedValue(new Error("409"))
    isAlreadyAnswered.mockReturnValue(true)
    queues({ data: { data: [request()], total: 1 } })
    render(<RefillQueue />)

    await userEvent.click(screen.getByTestId("refill-decide-approved"))
    await userEvent.click(screen.getByTestId("refill-confirm-submit"))

    expect(showToast).toHaveBeenCalledWith(
      "Someone at your practice already answered this request.",
      "error",
    )
  })

  it("asks to try again on any other failure", async () => {
    mutateAsync.mockRejectedValue(new Error("500"))
    isAlreadyAnswered.mockReturnValue(false)
    queues({ data: { data: [request()], total: 1 } })
    render(<RefillQueue />)

    await userEvent.click(screen.getByTestId("refill-decide-approved"))
    await userEvent.click(screen.getByTestId("refill-confirm-submit"))

    expect(showToast).toHaveBeenCalledWith("That didn't save. Try again.", "error")
  })

  it("lists recent decisions with what was decided", () => {
    queues({ data: { data: [], total: 0 } }, [
      request({ id: "req-9", status: "needs_visit", decided_at: "2026-09-27T15:00:00Z" }),
    ])
    render(<RefillQueue />)

    expect(screen.getByTestId("refill-recent-req-9")).toHaveTextContent("Needs a visit")
  })

  it("hides recent decisions when there are none", () => {
    queues({ data: { data: [], total: 0 } })
    render(<RefillQueue />)
    expect(screen.queryByTestId("refill-recent")).not.toBeInTheDocument()
  })
})
