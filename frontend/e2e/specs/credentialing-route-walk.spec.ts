// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Choosing the not-yet-paneled route gives her the steps that are for her.
 *
 * Both credentialing routes used to dead-end on placeholders — the step
 * holding her credentialing facts, and then again at the ending. The bodies
 * are covered by `stepBodies.test.tsx`, which walks every step of every route
 * and fails if any renders the not-built panel; the route→steps mapping is
 * covered by `routes.test.ts`.
 *
 * What only a browser shows is that picking this answer actually rebuilds the
 * stepper — so that is all this asserts. The stepper disables steps she has
 * not reached, and walking them for real means completing identity, contact,
 * rates and payers, which `new-practice-setup.spec.ts` already does.
 *
 * The wizard RESUMES where the shared pinned user left off, so this starts by
 * going back to the first step rather than assuming it is there. Assuming it
 * is what makes a spec pass alone and fail in a suite (PABLO-6738).
 */

import { expect, test } from "../fixtures/auth"

const SETUP = "/dashboard/billing/setup"

test("the not-yet-paneled route gets a record step before its ending @smoke", async ({
  signedInPage: page,
}) => {
  await page.goto(SETUP)

  // Step one is always reachable, whatever the saved progress.
  await page.getByRole("button", { name: /How you're paid/ }).click()
  await page.getByLabel("Clients pay me directly").check()
  await page.getByRole("button", { name: "Continue" }).click()
  await page.getByTestId("wants-credentialing").check()
  await page.getByRole("button", { name: "Continue" }).click()

  // "Your record" is what this route exists for, and what used to be a stub.
  await expect(page.getByRole("button", { name: /Your record/ })).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByRole("button", { name: /Done/ })).toBeVisible()
})
