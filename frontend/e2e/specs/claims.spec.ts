// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Filing a claim: from a visit nobody has billed to a claim at the payer.
 *
 * This is the path the whole billing surface exists for, and until now no
 * test walked it in a browser — which is how the fake clearinghouse came to
 * be serving an endpoint claims are no longer submitted to without anything
 * noticing.
 *
 * It stops at the clearinghouse, deliberately. Writing it further surfaced
 * two defects that were invisible until something drove the whole path
 * (PABLO-1qox, PABLO-ukzm): the receipts the clearinghouse posts back are
 * refused with a 400, and the pipeline's polling stage cannot read the
 * harness's answers. Asserting "paid" today would be asserting a bug. So the
 * spec proves what is true — the claim is built, scrubbed, filed, and
 * actually reaches the clearinghouse, once, on the endpoint it should — and
 * the rest lands with the fix.
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
  test("a covered visit is scrubbed, filed, and reaches the clearinghouse once", async ({
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

    await expect
      .poll(
        async () => {
          await page.reload()
          await page.getByTestId("billing-tab-claims").click()
          return (await claim.getAttribute("data-state")) ?? ""
        },
        { timeout: 60_000, intervals: [3_000] },
      )
      .toBe("submitted")
  })

})
