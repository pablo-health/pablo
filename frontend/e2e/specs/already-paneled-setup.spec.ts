// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The route the core billing practice walks: already on panels, ready to bill.
 *
 * Every other route had somewhere to end. This one stopped at a "not built
 * yet" panel on the step that decides who she can bill — which meant the
 * clearinghouse enrollment surface existed, worked, was tested, and was
 * unreachable from setup. A billing feature nobody can reach is not a billing
 * feature.
 *
 * What this proves is the path, not the payer machinery: that ticking
 * "Insurance I bill myself" walks her to the payer screen, that the screen is
 * the real one rather than a placeholder, and that the route now finishes.
 */

import { expect, test } from "../fixtures/auth"

test("an already-paneled practice reaches the payer screen and finishes", async ({
  signedInPage: page,
  api,
}) => {
  // This test walks the path without filling the forms on the way, which is
  // fine for what it proves — but the ending now reports actual readiness, and
  // a profile with gaps honestly says "underway" rather than "set up to bill".
  // So the readiness is arranged up front, deliberately, rather than the
  // assertion being softened to accept either answer.
  await api.patch("/api/practice/billing-profile", {
    legal_name: "Already Paneled LLC",
    billing_npi: "1234567893",
    tax_id: "123456789",
    tax_id_type: "ein",
    address_line1: "1 Panel St",
    city: "Savannah",
    state: "GA",
    postal_code: "31401",
    phone: "9125550123",
    contact_email: "paneled@example.com",
  })
  await api.patch("/api/users/me/professional-info", { npi_number: "1999999984" })

  await page.goto("/dashboard/billing/setup")

  await page.getByLabel("I bill insurance myself").check()
  await page.getByRole("button", { name: "Continue" }).click()
  await page.getByRole("button", { name: "Continue" }).click()

  // The lookup leads every route.
  await expect(page.getByRole("heading", { name: "Let's start with your NPI" })).toBeVisible()
  await page.getByRole("button", { name: "Continue" }).click()

  // The practice spine. Enrollment cannot be filed without it — the
  // clearinghouse registers the practice once, from the billing profile, and
  // refuses while it is incomplete. That is why these come first.
  await expect(
    page.getByRole("heading", { name: "How insurers identify your practice" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Continue" }).click()

  await expect(page.getByRole("heading", { name: "Where should insurers reach you?" })).toBeVisible()
  await page.getByRole("button", { name: "Continue" }).click()

  await expect(page.getByRole("heading", { name: "What you charge" })).toBeVisible()
  await page.getByRole("button", { name: "Continue" }).click()

  // The payer screen, and the real one: the card Settings mounts, carrying the
  // enrollment surface rather than a placeholder that says it is coming.
  await expect(page.getByRole("heading", { name: "Who can you bill today?" })).toBeVisible()
  await expect(page.getByText("Add a payer")).toBeVisible()
  await expect(page.getByText(/this step isn.t built yet/i)).toBeHidden()
  await page.getByRole("button", { name: "Continue" }).click()

  // And it ends somewhere, saying what happens next rather than congratulating
  // her on arriving — she was billing before she met us.
  await expect(page.getByRole("heading", { name: "You're set up to bill" })).toBeVisible()
  await expect(page.getByText(/enrollment requests sit with each payer/i)).toBeVisible()
})

test("the payer screen offers the enrollment surface, not a second copy of it", async ({
  signedInPage: page,
  api,
}) => {
  // Added through the API the way Settings would, then read on the wizard's
  // step: same card, same rows, one way to answer a payer.
  const stamp = Date.now().toString(36)
  await api.request("POST", "/api/payers", {
    name: `Wizard Payer ${stamp}`,
    payer_id: "STEDI",
  })

  // Land on the step directly rather than walking to it. The test above
  // finishes setup for this worker's clinician, and a finished wizard does not
  // re-ask the routing question — so replaying the walk here would be testing
  // resume, not the payer screen.
  await api.request("PUT", "/api/users/me/preferences", {
    billing_setup_state: ["own_insurance"],
    billing_setup_step: "payers",
  })

  await page.goto("/dashboard/billing/setup")

  await expect(page.getByRole("heading", { name: "Who can you bill today?" })).toBeVisible()
  await expect(page.getByText(`Wizard Payer ${stamp}`)).toBeVisible()
})
