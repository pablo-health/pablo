// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell route, `/portal/{slug}`, as a stranger meets it.
 *
 * Two things only a browser against the real stack can prove:
 *
 *   1. `/portal/*` is served to someone holding no clinician session. The
 *      patient on this page has a portal session or an invitation, never a
 *      sign-in, so a redirect to `/login` offers them a door they have no
 *      key to. That is what `builtInPublicPaths()` is for, and this is the
 *      test that fails if it is ever dropped.
 *   2. A slug that resolves to nothing renders the shell's own generic dead
 *      end — the same one an unknown and an unserved practice get, so the
 *      page is no oracle for which practices exist.
 *
 * Out of scope: the code-entry round trip, which the component suite pins
 * against the same contract in far less time, and slot content, which intake
 * and messaging own. The credential path itself is portal-auth.spec.ts.
 */

import { test, expect } from "../fixtures/auth"

const UNKNOWN_SLUG = "this-practice-does-not-exist-e2e"

test("an unresolvable slug shows the generic state @portal", async ({ page }) => {
  await page.goto(`/portal/${UNKNOWN_SLUG}`)

  await expect(page.getByTestId("portal-shell-unknown")).toBeVisible()
})

test("/portal/* loads without an auth redirect @portal", async ({ page }) => {
  await page.goto(`/portal/${UNKNOWN_SLUG}`)

  // An anonymous visitor reaches the shell's own state, not the clinician
  // sign-in screen.
  await expect(page).not.toHaveURL(/\/login/)
  await expect(page.getByTestId("portal-shell-unknown")).toBeVisible()
})
