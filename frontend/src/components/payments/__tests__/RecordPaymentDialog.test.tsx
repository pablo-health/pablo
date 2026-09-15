// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecordPaymentDialog tests.
 *
 * The behaviours worth pinning are the ones that encode a decision rather
 * than a layout: card is not offerable, `other` has to say what it was, the
 * amount is not capped at the balance, and a client already in credit does
 * not get a negative figure pre-filled.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RecordPaymentDialog } from "../RecordPaymentDialog"

const mockRecordPayment = vi.fn()

vi.mock("@/hooks/usePayments", () => ({
  useRecordPayment: () => ({ mutate: mockRecordPayment, isPending: false }),
}))

function renderDialog(balanceCents = 15_000) {
  return render(
    <RecordPaymentDialog
      patientId="patient-1"
      balanceCents={balanceCents}
      open
      onOpenChange={() => {}}
    />,
  )
}

async function chooseMethod(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(screen.getByRole("combobox", { name: /how it arrived/i }))
  await user.click(await screen.findByRole("option", { name: label }))
}

describe("RecordPaymentDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("records a cheque with its number", async () => {
    const user = userEvent.setup()
    renderDialog()

    await chooseMethod(user, "Check")
    await user.type(screen.getByLabelText(/check number/i), "1042")
    await user.click(screen.getByRole("button", { name: /record payment/i }))

    await waitFor(() => expect(mockRecordPayment).toHaveBeenCalled())
    expect(mockRecordPayment.mock.calls[0][0]).toMatchObject({
      patientId: "patient-1",
      data: { amount_cents: 15_000, method: "check", reference: "1042" },
    })
  })

  it("does not offer card", async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole("combobox", { name: /how it arrived/i }))

    expect(await screen.findByRole("option", { name: "Cash" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /card/i })).not.toBeInTheDocument()
  })

  it("refuses 'other' with nothing said about it", async () => {
    const user = userEvent.setup()
    renderDialog()

    await chooseMethod(user, "Something else")
    await user.click(screen.getByRole("button", { name: /record payment/i }))

    expect(await screen.findByRole("alert")).toHaveTextContent(/what this payment was/i)
    expect(mockRecordPayment).not.toHaveBeenCalled()
  })

  it("accepts more than the balance, because paying ahead is ordinary", async () => {
    const user = userEvent.setup()
    renderDialog(10_000)

    const amount = screen.getByLabelText(/amount/i)
    await user.clear(amount)
    await user.type(amount, "250.00")
    await chooseMethod(user, "Cash")
    await user.click(screen.getByRole("button", { name: /record payment/i }))

    await waitFor(() => expect(mockRecordPayment).toHaveBeenCalled())
    expect(mockRecordPayment.mock.calls[0][0].data.amount_cents).toBe(25_000)
  })

  it("does not prefill a negative amount for a client already in credit", () => {
    renderDialog(-5_000)

    // The balance is the practice's debt, not something to record a payment
    // for. Prefilling "-50.00" would be an amount nobody can act on.
    expect(screen.getByLabelText(/amount/i)).toHaveValue("0.00")
  })

  it("refuses an amount that is not a number", async () => {
    const user = userEvent.setup()
    renderDialog()

    const amount = screen.getByLabelText(/amount/i)
    await user.clear(amount)
    await user.type(amount, "abc")
    await chooseMethod(user, "Cash")
    await user.click(screen.getByRole("button", { name: /record payment/i }))

    expect(await screen.findByRole("alert")).toBeInTheDocument()
    expect(mockRecordPayment).not.toHaveBeenCalled()
  })
})
