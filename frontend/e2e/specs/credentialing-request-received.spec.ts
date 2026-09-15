// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The gap between asking for credentialing and anyone filing anything.
 *
 * Asking is a tick in the billing setup wizard. Filing the first application
 * is a person doing it, and that person may be days behind her. The tracker
 * renders nothing until a row exists — correctly, because telling a clinician
 * who never asked that she has no applications is telling her what she just
 * did — so for the one who DID ask, the screen said nothing at all about the
 * thing she had just requested.
 *
 * The component test covers the three states directly. What only a browser
 * shows is that the preference the wizard writes is the same one this screen
 * reads: two surfaces, two queries, one fact. That is the wiring that stays
 * invisible until somebody clicks it, and it is why this is a spec rather than
 * another unit test.
 *
 * Panel applications are created by the concierge operator console, which the
 * local stack does not run, so the empty board here is deterministic rather
 * than incidental.
 *
 * NOTE ON THE PREFERENCE WRITE: `PUT /api/users/me/preferences` is a FULL
 * REPLACE, and the account is shared by the whole run. Sending the one field
 * would blank this user's timezone and theme for every spec that follows, so
 * both tests below read, merge and write back. Do not shorten them.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"

const CREDENTIALING = "/dashboard/settings/credentialing"

/** Set just the credentialing flag, leaving every other preference intact. */
async function setWantsCredentialing(api: ApiClient, wants: boolean): Promise<void> {
  const current = await api.get<Record<string, unknown>>("/api/users/me/preferences")
  await api.put("/api/users/me/preferences", {
    ...current,
    billing_setup_wants_credentialing: wants,
  })
}

test("asking for credentialing is acknowledged before anything is filed @smoke", async ({
  signedInPage: page,
  api,
}) => {
  // The fact the wizard would write. Done through the API rather than by
  // walking the wizard because the walk is already covered by
  // credentialing-route-walk.spec.ts; what is under test here is the read side.
  await setWantsCredentialing(api, true)

  await page.goto(CREDENTIALING)

  const acknowledgement = page.getByTestId("credentialing-request-received")
  await expect(acknowledgement).toBeVisible({ timeout: 15_000 })
  await expect(acknowledgement).toContainText(/request is with us/i)

  // The two promises this copy must never make. A date is the one we cannot
  // keep, because the payer owns that clock; an update is one that nothing
  // currently sends.
  const text = (await acknowledgement.textContent()) ?? ""
  expect(text).not.toMatch(/\d+\s*(day|week|month|business)/i)
  expect(text).not.toMatch(/keep you (updated|posted)|notify you/i)
})

test("a clinician who never asked is not told she has no applications", async ({
  signedInPage: page,
  api,
}) => {
  await setWantsCredentialing(api, false)

  await page.goto(CREDENTIALING)

  await expect(page.getByTestId("credentialing-request-received")).toHaveCount(0)

  // ...and the front door for somebody who has not started is still the
  // lookup, which is the screen this page exists to open on.
  await expect(page.getByLabel(/NPI/i).first()).toBeVisible({ timeout: 15_000 })
})
