// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The NPI lookup, against a registry the stack can actually reach.
 *
 * Everything below this was previously unprovable end to end. The backend could
 * not verify any certificate — the stack pointed SSL_CERT_FILE at the fake mail
 * server's cert, which replaces the trust store rather than adding to it — so
 * the lookup failed before it left the container and the screen showed its
 * "registry isn't answering" state no matter what. Correct degradation, and a
 * useless test.
 *
 * The registry here is scripts/fake_nppes.py, not CMS. Two reasons. Reaching
 * the real one makes the suite fail on somebody else's bad afternoon. And its
 * records are real people's, which is not what a fixture should be — a name
 * asserted here would be a real clinician's name in a public repository.
 */

import { expect, test } from "../fixtures/auth"

const SETUP = "/dashboard/settings/credentialing"

test.describe("looking herself up", () => {
  test("an NPI already on her profile is looked up on arrival", async ({
    signedInPage: page,
    api,
  }) => {
    await api.request("PATCH", "/api/users/me/professional-info", {
      npi_number: "1999999984",
    })

    await page.goto(SETUP)

    // She types nothing. The screen becomes a question rather than a form.
    await expect(page.getByText("TEST THERAPIST, LCSW")).toBeVisible()
    await expect(page.getByText("Counselor, Mental Health")).toBeVisible()
    await expect(page.getByRole("button", { name: /that.s me/i })).toBeVisible()
  })

  test("the practice location is shown, not the mailing address", async ({
    signedInPage: page,
    api,
  }) => {
    // The fixture carries both, and they differ. A mailing address is often a
    // PO box; a payer application wants where she practises.
    await api.request("PATCH", "/api/users/me/professional-info", {
      npi_number: "1999999984",
    })

    await page.goto(SETUP)

    await expect(page.getByText("14 MILL STREET")).toBeVisible()
    await expect(page.getByText("PO BOX 900")).toBeHidden()
  })

  test("the licence the registry holds is shown, so she need not retype it", async ({
    signedInPage: page,
    api,
  }) => {
    await api.request("PATCH", "/api/users/me/professional-info", {
      npi_number: "1999999984",
    })

    await page.goto(SETUP)

    await expect(page.getByText("LCSW-4417")).toBeVisible()
  })

  test("the primary taxonomy wins when a record carries several", async ({
    signedInPage: page,
    api,
  }) => {
    // This fixture is a nurse practitioner whose record also lists Registered
    // Nurse. Taking the first would label a therapist by the wrong profession.
    await api.request("PATCH", "/api/users/me/professional-info", {
      npi_number: "1841151289",
    })

    await page.goto(SETUP)

    await expect(page.getByText("Marriage & Family Therapist")).toBeVisible()
    await expect(page.getByText("Registered Nurse")).toBeHidden()
  })
})

test.describe("when the answer is not a clean yes", () => {
  test("a number the registry has never heard of points at the number", async ({
    signedInPage: page,
  }) => {
    await page.goto(SETUP)

    await page.getByLabel("Your individual NPI").fill("1000000004")
    await page.getByRole("button", { name: /look it up/i }).click()

    await expect(page.getByText(/couldn.t find 1000000004/i)).toBeVisible()
    await expect(page.getByText(/easy to mistype/i)).toBeVisible()
  })

  test("an organisation NPI is named as one rather than confirmed", async ({
    signedInPage: page,
  }) => {
    // Tier 0 asks for an individual NPI and a billing NPI side by side, so
    // pasting the practice's number in is an easy mistake to make.
    await page.goto(SETUP)

    await page.getByLabel("Your individual NPI").fill("1234567893")
    await page.getByRole("button", { name: /look it up/i }).click()

    await expect(page.getByText(/organisation.s npi rather than a person.s/i)).toBeVisible()
  })

  test("a deactivated registration is flagged", async ({ signedInPage: page }) => {
    await page.goto(SETUP)

    await page.getByLabel("Your individual NPI").fill("1700000004")
    await page.getByRole("button", { name: /look it up/i }).click()

    await expect(page.getByText(/deactivated/i)).toBeVisible()
  })
})

test.describe("the two ways past it", () => {
  test("she can find herself by name and state", async ({ signedInPage: page }) => {
    await page.goto(SETUP)

    await page.getByRole("button", { name: /don.t know my npi/i }).click()
    await page.getByLabel("Last name").fill("THERAPIST")
    await page.getByLabel("State or territory").fill("NC")
    await page.getByRole("button", { name: "Search" }).click()

    // Both individual fixtures share the surname; the rows carry what tells
    // them apart.
    await expect(page.getByText("Counselor, Mental Health")).toBeVisible()
    await expect(page.getByText("Marriage & Family Therapist")).toBeVisible()

    await page
      .getByRole("listitem")
      .filter({ hasText: "Marriage & Family Therapist" })
      .getByRole("button", { name: /this is me/i })
      .click()

    await expect(page.getByRole("button", { name: /that.s me/i })).toBeVisible()
  })

  test("no NPI at all is a next step rather than an error", async ({ signedInPage: page }) => {
    await page.goto(SETUP)

    await page.getByRole("button", { name: /don.t have one/i }).click()

    await expect(page.getByText(/you.ll need an npi/i)).toBeVisible()
    await expect(page.getByText(/nothing else in your setup is blocked/i)).toBeVisible()
  })
})
