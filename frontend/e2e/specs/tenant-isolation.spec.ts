// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One practice cannot see another practice's data, through the app.
 *
 * Row-level security is the real boundary and is proven at the integration
 * layer against a NOBYPASSRLS role. What was never covered is the layer a user
 * actually goes through: an authenticated HTTP request carrying a real token,
 * reading a list that must not contain another practice's rows.
 *
 * THE ASSERTION IS SYMMETRIC ON PURPOSE. Both practices create a row of the
 * same kind, and each side's list must contain its own and not the other's.
 * "The other practice cannot see mine" ALONE proves nothing here, because most
 * of this product is scoped per USER inside a practice — a second clinician in
 * the SAME practice cannot see it either. Only the pair fails when the two
 * users collapse into one practice, because then each list holds both rows.
 *
 * WHY THERE IS ONLY ONE TEST, AND WHY IT IS APPOINTMENT TYPES. Every candidate
 * was mutation-checked by pointing the second user at an unseeded address, so
 * it auto-provisioned into the DEFAULT practice and the two shared one tenant.
 * A test that stays green under that is not evidence of anything:
 *
 *   appointment types  -> goes RED. Practice-scoped, so a shared practice is
 *                         immediately visible in the list. This is the test.
 *   patients           -> stays GREEN. `has_patient_access(id,
 *                         app.current_user_id)` means the two users cannot see
 *                         each other's patients whether or not they share a
 *                         practice, so the result is the same either way.
 *   direct GET by id   -> stays GREEN, for the same reason.
 *
 * The patient versions were written first and deleted after the mutation check
 * showed them inert. Do not re-add them here: they read like the strongest
 * tests in the file and are the weakest. Per-user patient access belongs in a
 * spec about per-user access.
 *
 * The tables with no row-level security at all — scheduling_policy,
 * practice_billing_profile, payers, payer_enrollments — are the other honest
 * candidates, isolated by the schema boundary alone. Each needs a second user
 * that has completed onboarding, since a bare emulator account's session does
 * not resolve to the practice.
 *
 * The second practice is created at stack bring-up by
 * backend/scripts/e2e_seed_second_practice.py.
 */

import { expect, test } from "../fixtures/auth"
import { giveBookableType } from "../fixtures/scenarios"

interface ListEnvelope {
  data: Array<{ id: string }>
}

async function visibleTypeIds(api: { get: <T>(path: string) => Promise<T> }): Promise<string[]> {
  return (await api.get<ListEnvelope>("/api/appointment-types")).data.map((row) => row.id)
}

test.describe("tenant isolation", () => {
  test("neither practice's appointment types appear in the other's list", async ({
    api,
    otherPracticeApi,
  }) => {
    const mine = await giveBookableType(api, { name: `Tenancy A ${Date.now()}` })
    const theirs = await giveBookableType(otherPracticeApi, {
      name: `Tenancy B ${Date.now()}`,
    })
    expect(theirs.id, "two practices must not share a row").not.toBe(mine.id)

    const mineVisible = await visibleTypeIds(api)
    expect(mineVisible, "this practice sees its own type").toContain(mine.id)
    expect(mineVisible, "and not the other practice's").not.toContain(theirs.id)

    const theirsVisible = await visibleTypeIds(otherPracticeApi)
    expect(theirsVisible, "the other practice sees its own type").toContain(theirs.id)
    expect(theirsVisible, "and not this practice's").not.toContain(mine.id)
  })
})
