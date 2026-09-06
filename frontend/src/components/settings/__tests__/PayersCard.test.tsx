// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PayersCard tests — the payer list and its deadlines in Settings.
 *
 * What matters here: the three deadlines are editable per payer with the
 * helper text that explains where the numbers come from, and an edit sends
 * only the field that changed. An open payer also shows where the practice
 * stands with it — each enrollment request and what the payer is waiting
 * on — with an "Enroll with payer" button that files the missing ones.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { DEADLINE_HELP, ENROLLMENT_HELP, PayersCard } from "../PayersCard"
import type { PayerEnrollmentListResponse, PayerResponse } from "@/types/coverage"

const mockUsePayers = vi.fn()
const mockUpdate = vi.fn()
const mockCreate = vi.fn()
const mockUseEnrollments = vi.fn()
const mockRequestEnrollments = vi.fn()

vi.mock("@/hooks/useCoverage", () => ({
  usePayers: (...args: unknown[]) => mockUsePayers(...args),
  useUpdatePayer: () => ({ mutate: mockUpdate, isPending: false }),
  useCreatePayer: () => ({ mutate: mockCreate, isPending: false }),
  usePayerEnrollments: (...args: unknown[]) => mockUseEnrollments(...args),
  useRequestPayerEnrollments: () => ({
    mutate: mockRequestEnrollments,
    isPending: false,
    error: null,
  }),
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

const ENROLLMENTS: PayerEnrollmentListResponse = {
  enrollment_status: "pending",
  data: [
    {
      transaction_type: "835",
      vendor_request_id: "enr-1",
      status: "provider_action_required",
      instructions: "Sign the EFT authorization form and upload the signed copy.",
      updated_at: "2026-09-08T14:02:11Z",
    },
    {
      transaction_type: "837P",
      vendor_request_id: "enr-2",
      status: "live",
      instructions: null,
      updated_at: "2026-09-08T14:02:11Z",
    },
  ],
}

describe("PayersCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePayers.mockReturnValue({ data: { data: [AETNA], total: 1 } })
    mockUseEnrollments.mockReturnValue({ data: undefined })
  })

  it("lists each payer with its filing window and enrollment status", () => {
    render(<PayersCard />)

    expect(screen.getByText("Aetna")).toBeInTheDocument()
    expect(
      screen.getByText(/Payer ID 60054 · files within 90 days · Not enrolled/),
    ).toBeInTheDocument()
  })

  it("fetches a payer's enrollments only once it is open", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    expect(mockUseEnrollments).not.toHaveBeenCalledWith("payer-1")

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(mockUseEnrollments).toHaveBeenCalledWith("payer-1")
    expect(screen.getByText(ENROLLMENT_HELP)).toBeInTheDocument()
  })

  it("shows each request, what the payer is waiting on, and the overall status", async () => {
    mockUseEnrollments.mockReturnValue({ data: ENROLLMENTS })
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))

    expect(screen.getByText("Enrollment in progress")).toBeInTheDocument()
    expect(screen.getByText("Remittance")).toBeInTheDocument()
    expect(screen.getByText("Needs your action")).toBeInTheDocument()
    expect(
      screen.getByText("Sign the EFT authorization form and upload the signed copy."),
    ).toBeInTheDocument()
    expect(screen.getByText("Claims")).toBeInTheDocument()
    expect(screen.getByText("Live")).toBeInTheDocument()
  })

  it("enrolls with the payer from its row", async () => {
    const user = userEvent.setup()
    render(<PayersCard />)

    await user.click(screen.getByRole("button", { name: /Aetna/ }))
    await user.click(screen.getByRole("button", { name: "Enroll with payer" }))

    expect(mockRequestEnrollments).toHaveBeenCalledWith({ payerRowId: "payer-1" })
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
