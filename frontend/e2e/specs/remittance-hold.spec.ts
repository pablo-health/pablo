// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A payer's numbers disagree, so the client is not billed — and a therapist
 * settles it.
 *
 * The unit tests cover the arithmetic and the repositories. This is the only
 * place the whole loop is driven at once: the pipeline reads the remittance,
 * the check refuses it, the database holds the row under the tenant's policy,
 * the panel puts it in front of the clinician who owns the claim, and their
 * answer writes the ledger row that was withheld. Every one of those is a
 * seam a unit test cannot cross.
 *
 * It is also the cheapest place this can ever be tested. The local stack's
 * fake clearinghouse manufactures the self-contradicting 835 on request, so
 * a case no real payer has ever sent us — and that the vendor's test payer
 * cannot produce, because it only ever pays in full — is a few lines here
 * rather than a deployment and a wait.
 *
 * What the payer contradicts itself about: it states a patient-responsibility
 * total for the claim, and itemises the same money across the service lines
 * as a contractual write-off. Two statements about who owes it, and they
 * disagree. Everything else in the document balances, so exactly one check
 * fires and the hold names one reason.
 *
 * Serial, and resetting: the fake's reset cancels queued receipts, so a spec
 * that resets mid-run would cancel an acknowledgement another spec is waiting
 * for. It also clears the armed outcome, which this spec must not leave
 * behind — every claim filed after it would come back contradicting itself.
 */

import { expect, test } from "../fixtures/auth"
import { clearinghouse } from "../fixtures/clearinghouse"
import {
  giveCoverage,
  giveInsurablePatient,
  givePracticeReadyToBill,
  giveVisitReadyToBill,
} from "../fixtures/scenarios"

const ROUTINE_VISIT = {
  service_code: "90834",
  diagnosis_codes: ["F41.1"],
  place_of_service: "11" as const,
  unit_count: 1,
}

const RATE_CENTS = 15000

/** What the fake's disagreeing 835 pays of the charge; the rest is the gap. */
const PAID_CENTS = 9000

interface LedgerRow {
  kind: string
  amount_cents: number
  claim_id: string | null
}

async function patientResponsibilityRows(
  api: { get<T>(path: string): Promise<T> },
  patientId: string,
): Promise<LedgerRow[]> {
  const ledger = await api.get<LedgerRow[]>(`/api/patients/${patientId}/charges`)
  return ledger.filter((row) => row.kind === "patient_resp")
}

test.describe.serial("a remittance that contradicts itself", () => {
  test("the payer is paid, the client is not billed, and the therapist settles it", async ({
    api,
    signedInPage: page,
  }) => {
    await clearinghouse.reset()
    // Armed BEFORE anything is filed. A claim filed through the app gets its
    // control number from the server, and the 835 follows five seconds after
    // submission — arming it afterwards would be racing that timer.
    await clearinghouse.expect835("disagreeing")

    await givePracticeReadyToBill(api)
    const patient = await giveInsurablePatient(api, { rate_cents: RATE_CENTS })
    await giveCoverage(api, patient.id)
    await giveVisitReadyToBill(api, patient.id, ROUTINE_VISIT)

    // Nothing on this client's ledger yet, so a row appearing later is this
    // claim's and not something the fixtures left behind.
    expect(await patientResponsibilityRows(api, patient.id)).toHaveLength(0)

    await page.goto("/dashboard/billing")
    const row = page.getByTestId("unbilled-row").filter({ hasText: patient.first_name })
    await expect(row).toBeVisible()
    await row.getByTestId("file-claim").click()
    await page.getByTestId("review-and-file").click()

    await page.getByTestId("billing-tab-claims").click()
    const claim = page.getByTestId("claims-tracker-row").first()
    await expect(claim).toBeVisible()
    const claimId = await claim.getAttribute("data-claim-id")
    expect(claimId).toBeTruthy()

    // The pipeline files it and the payer answers, both on the stack's own
    // one-minute interval. No sleeps: the assertion polls the thing it cares
    // about.
    const paidCents = async () => {
      const detail = await api.get<{ total_paid_cents: number }>(`/api/claims/${claimId}`)
      return detail.total_paid_cents
    }
    await expect.poll(paidCents, { timeout: 180_000, intervals: [5_000] }).toBe(PAID_CENTS)

    // (a) The payer's money posted. That is a fact about the practice's bank
    //     account and refusing to record it would only make a paid claim look
    //     unpaid.
    const detail = await api.get<{ total_paid_cents: number; total_charge_cents: number }>(
      `/api/claims/${claimId}`,
    )
    expect(detail.total_paid_cents).toBe(PAID_CENTS)
    expect(detail.total_charge_cents).toBe(RATE_CENTS)

    // (b) And the client's ledger gained NOTHING. This is the assertion the
    //     whole feature exists for: a real person was not billed an amount
    //     the engine's own arithmetic cannot corroborate.
    expect(await patientResponsibilityRows(api, patient.id)).toHaveLength(0)

    // (c) The disagreement is in front of the clinician who owns the claim,
    //     on the billing page, without them having gone looking for it.
    await page.reload()
    const hold = page.getByTestId("remittance-hold")
    await expect(hold).toBeVisible({ timeout: 30_000 })
    await expect(hold).toContainText(/not.*been billed/i)

    // (d) They take the payer at its word, and the row that was withheld is
    //     written — once.
    await hold.getByRole("button", { name: /bill .* as stated/i }).click()
    await expect(page.getByTestId("remittance-hold")).toBeHidden({ timeout: 30_000 })

    const billed = await patientResponsibilityRows(api, patient.id)
    expect(billed).toHaveLength(1)
    expect(billed[0].amount_cents).toBe(RATE_CENTS - PAID_CENTS)
    expect(billed[0].claim_id).toBe(claimId)
  })

  test("an ordinary remittance bills the client without asking anybody", async ({
    api,
    signedInPage: page,
  }) => {
    // The regression half. A check that held everything would also pass
    // every assertion in the test above, and would stop a practice billing
    // anyone at all.
    await clearinghouse.reset()
    await clearinghouse.expect835("partial")

    await givePracticeReadyToBill(api)
    const patient = await giveInsurablePatient(api, { rate_cents: RATE_CENTS })
    await giveCoverage(api, patient.id)
    await giveVisitReadyToBill(api, patient.id, ROUTINE_VISIT)

    await page.goto("/dashboard/billing")
    const row = page.getByTestId("unbilled-row").filter({ hasText: patient.first_name })
    await row.getByTestId("file-claim").click()
    await page.getByTestId("review-and-file").click()

    await expect
      .poll(async () => (await patientResponsibilityRows(api, patient.id)).length, {
        timeout: 180_000,
        intervals: [5_000],
      })
      .toBe(1)

    // And nothing was held, so nobody was asked.
    const holds = await api.get<{ total: number }>("/api/claims/holds")
    expect(holds.total).toBe(0)
  })
})
