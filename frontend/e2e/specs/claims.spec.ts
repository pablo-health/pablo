// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Filing a claim: from a visit nobody has billed to a claim at the payer.
 *
 * This is the path the whole billing surface exists for, and until now no
 * test walked it in a browser — which is how the fake clearinghouse came to
 * be serving an endpoint claims are no longer submitted to without anything
 * noticing.
 *
 * It now runs the whole way to paid. It used to stop at the clearinghouse,
 * because writing it further surfaced defects that made "paid" unreachable:
 * the harness answered none of the three paths the adapter calls, it served
 * the submission accept where a 277CA report belongs, and money could not be
 * booked from the state the claim was actually in (PABLO-1qox, PABLO-ukzm).
 *
 * Those are fixed, so the assertion that matters is here rather than in a
 * bead: a payer's money reaches a claim, and the claim says so. This is the
 * only test that crosses the pipeline, the database, the row policy and the
 * browser at once, which is the combination every one of those defects hid
 * behind.
 */

import { expect, test } from "../fixtures/auth"
import { clearinghouse } from "../fixtures/clearinghouse"
import {
  giveCoverage,
  giveInsurablePatient,
  givePracticeReadyToBill,
  giveVisitReadyToBill,
} from "../fixtures/scenarios"

/** A routine 50-minute psychotherapy visit, coded to full specificity. */
const ROUTINE_VISIT = {
  service_code: "90834",
  diagnosis_codes: ["F41.1"],
  place_of_service: "11" as const,
  unit_count: 1,
}

/**
 * What the visit is billed at. It lives on the client, not on the visit —
 * the claim resolves the rate the same way the charge action does (client
 * override first, then the appointment type's default), so a client with no
 * rate produces a claim with nothing to charge and cannot be filed.
 */
const RATE_CENTS = 15000

// Serial, and the order matters: resetting the fake cancels the receipts
// it has queued, so a test that resets would cancel the acknowledgement
// and the remittance another test is still waiting for. The one that
// waits goes last.
test.describe.serial("filing a claim", () => {
  test("a category-level diagnosis is stopped before anything is sent", async ({
    api,
    signedInPage: page,
  }) => {
    await clearinghouse.reset()
    await givePracticeReadyToBill(api)
    const patient = await giveInsurablePatient(api, { rate_cents: RATE_CENTS })
    await giveCoverage(api, patient.id)
    // F41 is the category. A payer wants F41.1 — the billable code beneath it.
    await giveVisitReadyToBill(api, patient.id, { ...ROUTINE_VISIT, diagnosis_codes: ["F41"] })

    await page.goto("/dashboard/billing")
    const row = page.getByTestId("unbilled-row").filter({ hasText: patient.first_name })
    await row.getByTestId("file-claim").click()

    // The scrub is the point: this is caught here, for free, instead of
    // coming back from the payer as a rejection in three weeks.
    await expect(page.getByTestId("review-and-file")).toBeDisabled()
    expect(await clearinghouse.submissions()).toHaveLength(0)
  })
  test("a covered visit is scrubbed, filed, acknowledged, and paid", async ({
    api,
    signedInPage: page,
  }) => {
    await clearinghouse.reset()
    await givePracticeReadyToBill(api)
    const patient = await giveInsurablePatient(api, { rate_cents: RATE_CENTS })
    await giveCoverage(api, patient.id)
    await giveVisitReadyToBill(api, patient.id, ROUTINE_VISIT)

    await page.goto("/dashboard/billing")

    // The visit is waiting to be billed, and because the client has coverage
    // the queue offers the claim rather than only the card.
    const row = page.getByTestId("unbilled-row").filter({ hasText: patient.first_name })
    await expect(row).toBeVisible()
    await row.getByTestId("file-claim").click()

    // Review is a stop, not a formality: nothing leaves until somebody
    // presses the second button.
    const file = page.getByTestId("review-and-file")
    await expect(file).toBeEnabled()
    expect(await clearinghouse.submissions()).toHaveLength(0)
    await file.click()

    // The claim is on the tracker, and the copy never says "Sent" before it
    // has been: it is queued, and still ours, until the clearinghouse has it.
    await page.getByTestId("billing-tab-claims").click()
    const claim = page.getByTestId("claims-tracker-row").first()
    await expect(claim).toBeVisible()
    await expect(claim).toHaveAttribute("data-state", "validated")
    expect(await clearinghouse.submissions()).toHaveLength(0)

    // Confirming a claim only marks it ready. The pipeline inside the API is
    // what sends it, on its own interval — a minute in this stack, five in a
    // default one — so this waits on the clearinghouse's log rather than on
    // the screen.
    await expect
      .poll(async () => (await clearinghouse.submissions()).length, {
        timeout: 120_000,
        intervals: [3_000],
      })
      .toBe(1)

    const [submission] = await clearinghouse.submissions()
    // The endpoint matters: submission moved to the vendor's native API, and
    // the harness served only the old path for a while with nothing noticing.
    expect(submission.path).toContain("/professional-claim-submissions")
    // And the key matters: it is what makes a resend a replay rather than a
    // second claim for the same visit.
    expect(submission.headers["idempotency-key"]).toBeTruthy()

    const stateNow = async () => {
      await page.reload()
      await page.getByTestId("billing-tab-claims").click()
      return (await claim.getAttribute("data-state")) ?? ""
    }

    // The clearinghouse acknowledges, then the payer's 835 arrives, both on
    // the harness's own timers and both delivered to the webhook. The
    // therapist's screen is what is asserted, because that is where the
    // answer has to appear — a claim that is paid in the database and still
    // says "submitted" on the tracker has not told anybody anything.
    //
    // NO INTERMEDIATE STATE IS ASSERTED, including "submitted". The claim
    // can be acknowledged before a poll ever observes it there, so asserting
    // the rung makes the test fail on a fast answer — which is the good
    // case. Whether the payer's own acknowledgement is seen at all is the
    // payer's business too: the test payer sends only a clearinghouse-sourced
    // 277CA, and an 835 books from wherever the claim is waiting.
    await expect.poll(stateNow, { timeout: 180_000, intervals: [5_000] }).toBe("paid")

    // And the money is the payer's, not a placeholder: what was charged is
    // what came back, read from the API the client itself reads.
    const claimId = await claim.getAttribute("data-claim-id")
    const paid = await api.get<{ total_paid_cents: number; total_charge_cents: number }>(
      `/api/claims/${claimId}`,
    )
    expect(paid.total_paid_cents).toBe(RATE_CENTS)
    expect(paid.total_paid_cents).toBe(paid.total_charge_cents)
  })

})
