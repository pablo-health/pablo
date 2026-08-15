// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecordOutcomeMeasureButton tests (PABLO-cwj)
 *
 * Covers the manual-entry on-ramp: per-item responses roll up to a live total,
 * the PHQ-9 item-9 safety callout surfaces non-blockingly, and submit POSTs
 * with source "manual" and the collected item_scores. Scoring/severity belong
 * to the backend, so the API client is mocked.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RecordOutcomeMeasureButton } from "../RecordOutcomeMeasureButton"
import { ToastProvider } from "@/components/ui/Toast"
import * as api from "@/lib/api/outcomeMeasures"

vi.mock("@/lib/api/outcomeMeasures")

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
  Wrapper.displayName = "RecordMeasureWrapper"
  return Wrapper
}

async function openDialog() {
  const user = userEvent.setup()
  render(<RecordOutcomeMeasureButton patientId="p1" />, {
    wrapper: createWrapper(),
  })
  await user.click(screen.getByRole("button", { name: /record score/i }))
  await screen.findByText(/record outcome measure/i)
  return user
}

describe("RecordOutcomeMeasureButton", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.createOutcomeMeasure).mockResolvedValue({
      id: "om1",
    } as Awaited<ReturnType<typeof api.createOutcomeMeasure>>)
  })

  it("defaults to PHQ-9 with nine items and a running total", async () => {
    const user = await openDialog()
    // The first PHQ-9 item prompt is present.
    expect(
      screen.getByText(/little interest or pleasure/i),
    ).toBeInTheDocument()
    // Answer item 1 with "Nearly every day" (3) → total reflects it.
    const item1Group = screen
      .getByText(/little interest or pleasure/i)
      .closest("div")!.parentElement!
    await user.click(within(item1Group).getByRole("button", { name: /3 ·/ }))
    expect(screen.getByText(/1\/9 answered/)).toBeInTheDocument()
  })

  it("surfaces the item-9 safety callout when endorsed", async () => {
    const user = await openDialog()
    const item9Group = screen
      .getByText(/better off dead/i)
      .closest("div")!.parentElement!
    // No callout before endorsement.
    expect(screen.queryByText(/assess safety/i)).not.toBeInTheDocument()
    await user.click(within(item9Group).getByRole("button", { name: /1 ·/ }))
    expect(screen.getAllByText(/assess safety/i).length).toBeGreaterThan(0)
  })

  it("POSTs source 'manual' with the collected item scores", async () => {
    const user = await openDialog()
    const item1Group = screen
      .getByText(/little interest or pleasure/i)
      .closest("div")!.parentElement!
    await user.click(within(item1Group).getByRole("button", { name: /2 ·/ }))
    await user.click(screen.getByRole("button", { name: /save score/i }))

    await waitFor(() => expect(api.createOutcomeMeasure).toHaveBeenCalled())
    const [patientId, body] = vi.mocked(api.createOutcomeMeasure).mock.calls[0]
    expect(patientId).toBe("p1")
    expect(body.instrument).toBe("phq9")
    expect(body.source).toBe("manual")
    expect(body.item_scores).toMatchObject({ "1": 2 })
    expect(typeof body.administered_at).toBe("string")
  })

  it("posts a known total when in total mode", async () => {
    const user = await openDialog()
    await user.click(screen.getByRole("button", { name: /known total/i }))
    await user.type(screen.getByLabelText(/total score/i), "14")
    await user.click(screen.getByRole("button", { name: /save score/i }))

    await waitFor(() => expect(api.createOutcomeMeasure).toHaveBeenCalled())
    const [, body] = vi.mocked(api.createOutcomeMeasure).mock.calls[0]
    expect(body.total_score).toBe(14)
    expect(body.item_scores).toBeUndefined()
  })

  describe("read-only deployment mode", () => {
    afterEach(() => {
      vi.unstubAllEnvs()
    })

    it("hides the Record score trigger when read-only", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
      render(<RecordOutcomeMeasureButton patientId="p1" />, {
        wrapper: createWrapper(),
      })

      expect(
        screen.queryByRole("button", { name: /record score/i }),
      ).not.toBeInTheDocument()
    })

    it("shows the Record score trigger when the flag is unset", () => {
      render(<RecordOutcomeMeasureButton patientId="p1" />, {
        wrapper: createWrapper(),
      })

      expect(
        screen.getByRole("button", { name: /record score/i }),
      ).toBeInTheDocument()
    })
  })
})
