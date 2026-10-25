// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Floating-block hooks: one cached read per date, flattened for the week,
 * and every write that can move a block — an exception on the block itself,
 * or an appointment booked or moved around it — invalidating those reads.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import {
  useClearFloatingBlockException,
  useFloatingBlocks,
  useFloatingBlocksForDates,
  useSetFloatingBlockException,
} from "../useFloatingBlocks"
import { useCreateAppointment, useUpdateAppointment } from "../useAppointments"
import * as availabilityApi from "@/lib/api/availability"
import * as schedulingApi from "@/lib/api/scheduling"
import { queryKeys } from "@/lib/api/queryKeys"
import type { FloatingBlock, FloatingBlocksResponse } from "@/types/availability"
import type { AppointmentResponse } from "@/types/scheduling"

vi.mock("@/lib/api/availability")
vi.mock("@/lib/api/scheduling")

function block(date: string, overrides: Partial<FloatingBlock> = {}): FloatingBlock {
  return {
    rule_id: "rule-lunch",
    label: "Lunch",
    date,
    state: "floating",
    duration_minutes: 60,
    window_start: "11:30",
    window_end: "14:00",
    start: `${date}T16:30:00Z`,
    end: `${date}T17:30:00Z`,
    ...overrides,
  }
}

function day(date: string, blocks: FloatingBlock[]): FloatingBlocksResponse {
  return { date, blocks }
}

const newQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

function createWrapper(queryClient: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("useFloatingBlocks", () => {
  it("reads one date and caches it under that date", async () => {
    const queryClient = newQueryClient()
    vi.mocked(availabilityApi.getFloatingBlocks).mockResolvedValue(
      day("2026-11-03", [block("2026-11-03")]),
    )

    const { result } = renderHook(() => useFloatingBlocks("2026-11-03"), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(availabilityApi.getFloatingBlocks).toHaveBeenCalledWith("2026-11-03", undefined)
    expect(result.current.data?.blocks.map((b) => b.label)).toEqual(["Lunch"])
    expect(
      queryClient.getQueryData(queryKeys.availability.floatingBlocks("2026-11-03")),
    ).toEqual(day("2026-11-03", [block("2026-11-03")]))
  })

  it("asks for nothing without a date", () => {
    renderHook(() => useFloatingBlocks(""), { wrapper: createWrapper(newQueryClient()) })
    expect(availabilityApi.getFloatingBlocks).not.toHaveBeenCalled()
  })
})

describe("useFloatingBlocksForDates", () => {
  it("reads each date and flattens the blocks", async () => {
    vi.mocked(availabilityApi.getFloatingBlocks).mockImplementation(async (date) =>
      day(date, date === "2026-11-04" ? [] : [block(date)]),
    )

    const { result } = renderHook(
      () => useFloatingBlocksForDates(["2026-11-03", "2026-11-04", "2026-11-05"]),
      { wrapper: createWrapper(newQueryClient()) },
    )

    await waitFor(() => expect(result.current).toHaveLength(2))
    expect(result.current.map((b) => b.date)).toEqual(["2026-11-03", "2026-11-05"])
    expect(availabilityApi.getFloatingBlocks).toHaveBeenCalledTimes(3)
  })

  it("asks for nothing while disabled", () => {
    const { result } = renderHook(() => useFloatingBlocksForDates(["2026-11-03"], false), {
      wrapper: createWrapper(newQueryClient()),
    })
    expect(result.current).toEqual([])
    expect(availabilityApi.getFloatingBlocks).not.toHaveBeenCalled()
  })
})

describe("exception writes", () => {
  it("skipping a date sends skip and invalidates the cached days", async () => {
    const queryClient = newQueryClient()
    vi.mocked(availabilityApi.setFloatingBlockException).mockResolvedValue(
      {} as Awaited<ReturnType<typeof availabilityApi.setFloatingBlockException>>,
    )
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

    const { result } = renderHook(() => useSetFloatingBlockException(), {
      wrapper: createWrapper(queryClient),
    })
    await result.current.mutateAsync({
      ruleId: "rule-lunch",
      date: "2026-11-03",
      exception: { skip: true },
    })

    expect(availabilityApi.setFloatingBlockException).toHaveBeenCalledWith(
      "rule-lunch",
      "2026-11-03",
      { skip: true },
      undefined,
    )
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.availability.all })
  })

  it("putting a block back clears that date and invalidates the cached days", async () => {
    const queryClient = newQueryClient()
    vi.mocked(availabilityApi.clearFloatingBlockException).mockResolvedValue(
      {} as Awaited<ReturnType<typeof availabilityApi.clearFloatingBlockException>>,
    )
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

    const { result } = renderHook(() => useClearFloatingBlockException(), {
      wrapper: createWrapper(queryClient),
    })
    await result.current.mutateAsync({ ruleId: "rule-lunch", date: "2026-11-03" })

    expect(availabilityApi.clearFloatingBlockException).toHaveBeenCalledWith(
      "rule-lunch",
      "2026-11-03",
      undefined,
    )
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.availability.all })
  })
})

describe("appointment writes move the bands", () => {
  const appointment = {
    id: "appt-1",
    start_at: "2026-11-03T16:30:00Z",
    end_at: "2026-11-03T17:15:00Z",
  } as AppointmentResponse

  it("booking an appointment invalidates the floating blocks", async () => {
    const queryClient = newQueryClient()
    vi.mocked(schedulingApi.createAppointment).mockResolvedValue(appointment)
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

    const { result } = renderHook(() => useCreateAppointment(), {
      wrapper: createWrapper(queryClient),
    })
    await result.current.mutateAsync({
      patient_id: "patient-1",
      title: "Session",
      start_at: appointment.start_at,
      end_at: appointment.end_at,
      duration_minutes: 45,
    })

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.availability.floatingBlocksAll(),
    })
  })

  it("moving an appointment invalidates the floating blocks", async () => {
    const queryClient = newQueryClient()
    vi.mocked(schedulingApi.updateAppointment).mockResolvedValue(appointment)
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

    const { result } = renderHook(() => useUpdateAppointment(), {
      wrapper: createWrapper(queryClient),
    })
    await result.current.mutateAsync({
      appointmentId: "appt-1",
      data: { start_at: "2026-11-03T18:00:00Z", end_at: "2026-11-03T18:45:00Z" },
    })

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.availability.floatingBlocksAll(),
      }),
    )
  })
})
