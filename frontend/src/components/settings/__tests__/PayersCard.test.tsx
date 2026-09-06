// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PayersCard tests — the payer list and its deadlines in Settings.
 *
 * What matters here: the three deadlines are editable per payer with the
 * helper text that explains where the numbers come from, and an edit sends
 * only the field that changed.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { DEADLINE_HELP, PayersCard } from "../PayersCard"
import type { PayerResponse } from "@/types/coverage"

const mockUsePayers = vi.fn()
const mockUpdate = vi.fn()
const mockCreate = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePayers: (...args: unknown[]) => mockUsePayers(...args),
  useUpdatePayer: () => ({ mutate: mockUpdate, isPending: false }),
  useCreatePayer: () => ({ mutate: mockCreate, isPending: false }),
}))

const AETNA: PayerResponse = {
  id: "payer-1",
  name: "Aetna",
  payer_id: "60054",
  clearinghouse_payer_id: null,
  is_carveout: false,
  carveout_of: null,
  enrollment_status: "none",
  timely_filing_days: 90,
  corrected_claim_days: 90,
  appeal_days: 180,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
}

describe("PayersCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePayers.mockReturnValue({ data: { data: [AETNA], total: 1 } })
  })

  it("lists each payer with its filing window", () => {
    render(<PayersCard />)

    expect(screen.getByText("Aetna")).toBeInTheDocument()
    expect(screen.getByText(/Payer ID 60054 · files within 90 days/)).toBeInTheDocument()
  })

  it("opens a payer to edit the three deadlines, with the helper text", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(screen.getByLabelText("Timely filing (days)")).toHaveValue(90)
    expect(screen.getByLabelText("Corrected claim (days)")).toHaveValue(90)
    expect(screen.getByLabelText("Appeal (days)")).toHaveValue(180)
    expect(screen.getByText(DEADLINE_HELP)).toBeInTheDocument()
  })

  it("sends only the deadline that changed", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    const filing = screen.getByLabelText("Timely filing (days)")
    await user.clear(filing)
    await user.type(filing, "365")
    await user.tab()

    expect(mockUpdate).toHaveBeenCalledWith({
      id: "payer-1",
      data: { timely_filing_days: 365 },
    })
  })

  it("adds a payer by name and payer id", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Add a payer/ }))
    await user.type(screen.getByLabelText("Name"), "Cigna")
    await user.type(screen.getByLabelText("Payer ID"), "62308")
    await user.click(screen.getByRole("button", { name: "Add" }))

    expect(mockCreate).toHaveBeenCalledWith(
      { name: "Cigna", payer_id: "62308" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })
})
