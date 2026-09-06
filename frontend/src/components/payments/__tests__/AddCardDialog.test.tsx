// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AddCardDialog — the confirmSetup call shape.
 *
 * Stripe.js accepts two shapes for confirming a SetupIntent, and mixing them
 * throws `IntegrationError` synchronously, before any network call:
 *
 *   1. `<Elements options={{clientSecret}}>` + `confirmSetup({elements})`
 *      — the secret comes from the Elements group.
 *   2. `<Elements options={{mode: "setup", ...}}>` + `elements.submit()` then
 *      `confirmSetup({elements, clientSecret})` — the deferred-intent flow.
 *
 * This dialog builds Elements with a client secret, so it must use shape 1.
 * It previously passed `clientSecret` as well, which selects shape 2 and
 * additionally requires `elements.submit()`. The result was that "Save card"
 * did nothing at all: the throw happened before Stripe was contacted, so no
 * SetupIntent was ever confirmed and no card was ever attached. Nothing in the
 * UI said so beyond a generic failure.
 *
 * These tests pin the call shape rather than the outcome, because the outcome
 * (a real card attaching) can only be exercised against Stripe in the browser
 * e2e — and that e2e is the only place this was caught.
 */
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

const confirmSetup = vi.fn()
const mutateAsync = vi.fn()

vi.mock("@stripe/stripe-js/pure", () => ({
  loadStripe: vi.fn(() => Promise.resolve({})),
}))

vi.mock("@stripe/react-stripe-js", () => ({
  Elements: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PaymentElement: () => <div data-testid="payment-element" />,
  useStripe: () => ({ confirmSetup }),
  useElements: () => ({ submit: vi.fn() }),
}))

vi.mock("@/hooks/usePayments", () => ({
  useStartCardSetup: () => ({
    mutateAsync: vi.fn(() =>
      // Both values are inert here: Elements and loadStripe are mocked above,
      // so nothing parses them. Kept deliberately shapeless so secret scanning
      // has nothing to recognise.
      Promise.resolve({
        client_secret: "unused",
        publishable_key: "unused",
      }),
    ),
  }),
  useCompleteCardSetup: () => ({ mutateAsync, isPending: false }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import { AddCardDialog } from "../AddCardDialog"

async function openDialogAndSave() {
  render(<AddCardDialog patientId="p-1" open onOpenChange={() => {}} />)
  const save = await screen.findByRole("button", { name: /save card/i })
  await userEvent.click(save)
}

describe("AddCardDialog confirmSetup call", () => {
  beforeEach(() => {
    confirmSetup.mockReset()
    mutateAsync.mockReset()
    confirmSetup.mockResolvedValue({ setupIntent: { id: "seti_123" } })
    mutateAsync.mockResolvedValue({})
  })

  it("does not pass clientSecret alongside elements", async () => {
    await openDialogAndSave()
    await waitFor(() => expect(confirmSetup).toHaveBeenCalled())

    const args = confirmSetup.mock.calls[0][0]
    expect(
      args,
      "Elements already carries the client secret; passing it again selects the deferred-intent shape and throws IntegrationError",
    ).not.toHaveProperty("clientSecret")
  })

  it("passes the elements group and keeps entry on the page", async () => {
    await openDialogAndSave()
    await waitFor(() => expect(confirmSetup).toHaveBeenCalled())

    const args = confirmSetup.mock.calls[0][0]
    expect(args.elements, "the Elements group carries the secret").toBeDefined()
    expect(args.redirect, "card entry stays on this page").toBe("if_required")
  })

  it("sends the SetupIntent id on once Stripe confirms", async () => {
    await openDialogAndSave()
    await waitFor(() => expect(mutateAsync).toHaveBeenCalled())

    expect(mutateAsync).toHaveBeenCalledWith({
      patientId: "p-1",
      setupIntentId: "seti_123",
    })
  })

  it("surfaces Stripe's own message and sends nothing on when it refuses", async () => {
    confirmSetup.mockResolvedValue({ error: { message: "Your card was declined." } })
    await openDialogAndSave()

    expect(await screen.findByText("Your card was declined.")).toBeVisible()
    expect(mutateAsync, "a refused card is never recorded").not.toHaveBeenCalled()
  })
})
