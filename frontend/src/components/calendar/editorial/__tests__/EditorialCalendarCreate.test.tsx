// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The seam nothing covered: the REAL appointment hooks driving the REAL
 * calendar.
 *
 * `useAppointments.test.tsx` exercises the hooks with a hand-seeded cache and
 * no calendar mounted. `EditorialCalendar.test.tsx` mounts the calendar with
 * `useAppointments` mocked out. Booking an appointment and seeing it land on
 * the grid crosses both, so neither one can catch it failing.
 *
 * The client is built the way the app builds it (`createAppQueryClient`:
 * staleTime 60s, refetchOnWindowFocus off), so nothing refetches on its own
 * and the mutation's own cache handling is the only thing that can put the
 * appointment on the grid.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClientProvider } from "@tanstack/react-query"
import { createAppQueryClient } from "@/components/providers"
import { EditorialCalendar } from "../EditorialCalendar"
import { ToastProvider } from "@/components/ui/Toast"
import { useCreateAppointment } from "@/hooks/useAppointments"
import type { AppointmentResponse } from "@/types/scheduling"

const listAppointments = vi.fn()
const createAppointment = vi.fn()

vi.mock("@/lib/api/scheduling", () => ({
  listAppointments: (...a: unknown[]) => listAppointments(...a),
  createAppointment: (...a: unknown[]) => createAppointment(...a),
  updateAppointment: vi.fn(),
}))

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({
    data: { data: [{ id: "p1", first_name: "Ada", last_name: "Lovelace" }] },
  }),
}))

vi.mock("@/lib/config", () => ({ useConfig: () => ({ dataMode: "api" }) }))
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: { uid: "u1" }, loading: false }) }))

function at(hour: number, minute = 0): string {
  const d = new Date()
  d.setHours(hour, minute, 0, 0)
  return d.toISOString()
}

function makeAppointment(): AppointmentResponse {
  return {
    id: "new-appt",
    user_id: "u1",
    patient_id: "p1",
    title: "Ada Lovelace — Individual",
    patient_name: "Ada Lovelace",
    start_at: at(13, 15),
    end_at: at(14, 0),
    duration_minutes: 45,
    status: "confirmed",
    session_type: "individual",
  } as AppointmentResponse
}

/** Mounts the calendar and exposes a button that books through the real hook. */
function Harness() {
  const create = useCreateAppointment()
  return (
    <ToastProvider>
      <button onClick={() => create.mutate({} as never)}>book</button>
      <EditorialCalendar
        theme={"light" as never}
        onSelectSlot={vi.fn()}
        onSelectAppointment={vi.fn()}
        onCreateNew={vi.fn()}
      />
    </ToastProvider>
  )
}

/** Stands in for the server's stored rows, so a list reflects prior creates. */
let serverRows: AppointmentResponse[] = []

beforeEach(() => {
  vi.clearAllMocks()
  serverRows = []
  listAppointments.mockImplementation(async () => ({
    data: [...serverRows],
    total: serverRows.length,
  }))
  createAppointment.mockImplementation(async () => {
    const a = makeAppointment()
    serverRows.push(a)
    return a
  })
})

describe("booking through the real hook puts the appointment on the calendar", () => {
  it("appears without a reload", async () => {
    const qc = createAppQueryClient(() => {})
    render(
      <QueryClientProvider client={qc}>
        <Harness />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(listAppointments).toHaveBeenCalled())
    expect(screen.queryByText("Ada Lovelace")).toBeNull()

    screen.getByRole("button", { name: "book" }).click()

    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeInTheDocument(), {
      timeout: 4000,
    })
  })

  // The case the e2e suite hits and a human almost never does: the calendar's
  // FIRST list fetch is still in flight when the create resolves. That response
  // was issued before the appointment existed, so it cannot contain it — and
  // the query is already fetching, so the invalidation behind the cache append
  // is deduped against the outstanding request instead of starting a fresh one.
  // The appointment is then absent until something else refetches, and nothing
  // does: staleTime is 60s, refetchOnWindowFocus is off, there is no polling.
  //
  // A human takes seconds to fill the modal, so the first fetch has long
  // settled and the race never opens. The e2e books ~2s after load and hits it
  // every time.
  it("survives the initial list fetch landing AFTER the create", async () => {
    let releaseFirstList: (() => void) | undefined
    let call = 0
    listAppointments.mockImplementation(async () => {
      call += 1
      if (call === 1) {
        // Issued before the appointment existed; held open across the create.
        await new Promise<void>((r) => {
          releaseFirstList = r
        })
        return { data: [], total: 0 }
      }
      return { data: [...serverRows], total: serverRows.length }
    })

    const qc = createAppQueryClient(() => {})
    render(
      <QueryClientProvider client={qc}>
        <Harness />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(listAppointments).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(releaseFirstList).toBeDefined())

    screen.getByRole("button", { name: "book" }).click()
    await waitFor(() => expect(createAppointment).toHaveBeenCalled())

    // Let the stale response land, after the create.
    releaseFirstList!()

    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeInTheDocument(), {
      timeout: 4000,
    })
  })
})
