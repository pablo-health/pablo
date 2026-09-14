// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The confirmation that stands between a press and a payer's money moving.
 *
 * Filing an 835 enrollment tells a payer to send its remittances here, and
 * they then stop arriving wherever they arrive today. That consequence lands
 * outside Pablo — on an old clearinghouse, a billing service, or the platform
 * that has been handling her insurance side — which is what makes it different
 * from everything else on the payers card.
 *
 * Bug classes here, all of which fail quietly:
 *
 * * **The general action redirecting ERA.** One button filing eligibility,
 *   claims and remittances together makes the consequential one look like the
 *   other two.
 * * **Reassuring her about routing we have not checked.** Some payers route by
 *   tax id, so enrolling moves every clinician under it. We cannot see that
 *   from this card, and saying nothing reads as "no problem".
 * * **Filing under an identity she never saw.** "Enrolled" is not inspectable
 *   afterwards; a request under the wrong NPI is discovered when the money
 *   does not arrive.
 * * **A tick carrying across payers.** Confirming for one payer must not
 *   pre-confirm the next.
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { PayerResponse } from "@/types/coverage"
import { RemittanceCutoverDialog } from "../RemittanceCutoverDialog"

let profile: Record<string, unknown> | undefined

vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: () => ({ data: profile }),
}))

function payer(overrides: Partial<PayerResponse> = {}): PayerResponse {
  return {
    id: "p1",
    name: "Aetna",
    payer_id: "60054",
    clearinghouse_payer_id: null,
    is_carveout: false,
    carveout_of: null,
    enrollment_status: "not_started",
    enroll_eligibility: true,
    enroll_claims: true,
    enroll_remittance: true,
    timely_filing_days: 90,
    corrected_claim_days: 180,
    appeal_days: 180,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as PayerResponse
}

beforeEach(() => {
  vi.clearAllMocks()
  profile = { billing_npi: "1999999984", tax_id_last4: "9714" }
})

describe("what it tells her before anything moves", () => {
  it("names the payer being asked", () => {
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )

    expect(screen.getByText(/Aetna · ID 60054/)).toBeInTheDocument()
  })

  it("shows the identity the request goes under", () => {
    // Discovered otherwise when the money does not arrive.
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )

    expect(screen.getByTestId("cutover-identity")).toHaveTextContent("NPI 1999999984")
    expect(screen.getByTestId("cutover-identity")).toHaveTextContent("ending in 9714")
  })

  it("says what stops receiving them", () => {
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )

    expect(screen.getByText(/may stop the reports from going to/i)).toBeInTheDocument()
  })

  it("admits it cannot tell how the payer routes them", () => {
    // The reassuring guess is the dangerous one: a tax-id-routed payer moves
    // every clinician under it, and a colleague finds out by not being paid.
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )

    expect(screen.getByTestId("cutover-routing-unknown")).toHaveTextContent(
      /only for this NPI or for every clinician using this tax ID/i,
    )
  })
})

describe("what it will not let her do", () => {
  it("will not file until she confirms she knows where they go today", () => {
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )

    expect(screen.getByTestId("cutover-confirm")).toBeDisabled()
  })

  it("files once she has", async () => {
    const onConfirm = vi.fn()
    const user = userEvent.setup()
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={onConfirm} />,
    )

    await user.click(screen.getByTestId("cutover-understood"))
    await user.click(screen.getByTestId("cutover-confirm"))

    expect(onConfirm).toHaveBeenCalled()
  })

  it("refuses while the practice identity is incomplete", async () => {
    // It would otherwise be filed under whatever happens to be on file, which
    // is the same failure as filing under the wrong NPI.
    profile = { billing_npi: null, tax_id_last4: "9714" }
    const user = userEvent.setup()
    render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )

    await user.click(screen.getByTestId("cutover-understood"))

    expect(screen.getByTestId("cutover-identity-missing")).toBeInTheDocument()
    expect(screen.getByTestId("cutover-confirm")).toBeDisabled()
  })

  it("starts unticked again for the next payer", async () => {
    // Otherwise a second payer's remittances move on a box she ticked about
    // the first. The card mounts this only while she is confirming, so closing
    // it discards the tick — which this reproduces by unmounting rather than
    // by toggling a prop, because that is what actually happens.
    const user = userEvent.setup()
    const { unmount } = render(
      <RemittanceCutoverDialog payer={payer()} open onOpenChange={() => {}} onConfirm={() => {}} />,
    )
    await user.click(screen.getByTestId("cutover-understood"))
    expect(screen.getByTestId("cutover-confirm")).toBeEnabled()
    unmount()

    render(
      <RemittanceCutoverDialog
        payer={payer({ id: "p2", name: "Cigna" })}
        open
        onOpenChange={() => {}}
        onConfirm={() => {}}
      />,
    )

    expect(screen.getByTestId("cutover-confirm")).toBeDisabled()
  })
})
