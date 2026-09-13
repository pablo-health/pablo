// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which NPI a practice bills under, and the two legitimate answers.
 *
 * A provider record is created per unique (NPI, tax id) pair billed under.
 * For an entity that pair is its organisation NPI and its EIN; for a sole
 * proprietor it is her own NPI and her own SSN. Both are real, and the product
 * used to serve only the first: the billing NPI field was hidden whenever the
 * tax id type was SSN, and the clearinghouse then refused to register her for
 * the field she had no way to fill. She could not get out of it from inside
 * the product.
 *
 * That is fixed, and these are the tests that keep it fixed. The bug class is
 * a **silent asymmetry** — a field the UI reasons about one way and the
 * enrollment path reasons about another — so what is asserted here is the
 * record rather than the screen: what actually landed in the billing profile,
 * under each structure.
 *
 * The failure mode worth being loudest about is a wrong PREFILL rather than a
 * missing one. Her personal NPI offered to an entity would be the wrong answer
 * accepted in one click, and the (NPI, tax id) pair is what a payer hangs
 * remittance routing on.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"

const IDENTITY = "/dashboard/settings/billing-profile"
const PAYERS = "/dashboard/settings/insurance"

/** Hers, and the practice's — two different numbers on purpose, so "it used
 * the right one" is provable rather than a coincidence of both being set. */
const OWN_NPI = "1999999984"
const ORG_NPI = "1234567893"

interface BillingProfile {
  billing_npi: string | null
  tax_id_last4: string | null
  tax_id_type: string | null
  clearinghouse_provider_id: string | null
}

interface Me {
  npi_number: string | null
}

async function setStructure(api: ApiClient, taxIdType: "ssn" | "ein") {
  await api.request("PATCH", "/api/practice/billing-profile", {
    tax_id: "123456789",
    tax_id_type: taxIdType,
    billing_npi: null,
  })
}

test("a sole proprietor can fill the billing NPI she is judged on", async ({
  signedInPage: page,
  api,
}) => {
  // The dead end this closes: hidden on an SSN, then reported missing.
  await setStructure(api, "ssn")
  await page.goto(IDENTITY)

  await expect(page.getByLabel("Billing NPI")).toBeVisible()
})

test("she can answer it with her own NPI in one click, and that is what is stored", async ({
  signedInPage: page,
  api,
}) => {
  // Given her one rather than skipping when she has none. This is THE test for
  // the dead end — a clinician billing under her own SSN reaching a
  // registerable identity — and a skip here would read as green while proving
  // nothing.
  await api.request("PATCH", "/api/users/me/professional-info", { npi_number: OWN_NPI })
  await setStructure(api, "ssn")
  await page.goto(IDENTITY)

  await page.getByRole("button", { name: "Use my individual NPI" }).click()
  await page.getByRole("button", { name: "Save" }).first().click()

  // The record, not the screen.
  await expect
    .poll(async () => (await api.get<BillingProfile>("/api/practice/billing-profile")).billing_npi)
    .toBe(OWN_NPI)
})

test("an entity is never offered her personal NPI", async ({ signedInPage: page, api }) => {
  // Under an EIN the practice is the biller, so her own NPI is the wrong
  // answer — and a wrong prefill is worse than none, because it is accepted in
  // one click and discovered when the money does not arrive.
  await setStructure(api, "ein")
  await page.goto(IDENTITY)

  await expect(page.getByLabel("Billing NPI")).toBeVisible()
  await expect(page.getByRole("button", { name: "Use my individual NPI" })).toBeHidden()
})

test("an entity's own NPI is what gets stored for it", async ({ signedInPage: page, api }) => {
  await api.request("PATCH", "/api/users/me/professional-info", { npi_number: OWN_NPI })
  await setStructure(api, "ein")
  await page.goto(IDENTITY)

  await page.getByLabel("Billing NPI").fill(ORG_NPI)
  await page.getByRole("button", { name: "Save" }).first().click()

  await expect
    .poll(async () => (await api.get<BillingProfile>("/api/practice/billing-profile")).billing_npi)
    .toBe(ORG_NPI)

  // The two are genuinely distinct rather than the same value twice — which
  // is the whole claim, and would pass vacuously if she had no NPI of her own.
  const after = await api.get<BillingProfile>("/api/practice/billing-profile")
  expect(after.billing_npi).not.toBe(OWN_NPI)
  expect((await api.get<Me>("/api/users/me/status")).npi_number).toBe(OWN_NPI)
})

test("credentialing does not file an enrollment behind her", async ({ signedInPage: page, api }) => {
  // Separate milestones: preparing a credentialing record says nothing about
  // wanting a payer's remittances rerouted here. Asserted as an ABSENCE,
  // because the failure would be silent — an 835 filed by a step that never
  // mentioned one.
  // A payer the clearinghouse directory actually knows, so the control at the
  // end can really file something. Remittance is the transaction this entry
  // requires an enrollment for, and it starts switched off.
  const name = `Identity Test ${Date.now().toString(36)}`
  await api.request("PATCH", "/api/practice/billing-profile", {
    legal_name: "Identity Test Practice",
    billing_npi: ORG_NPI,
    tax_id: "123456789",
    tax_id_type: "ein",
    address_line1: "1 Test St",
    city: "Savannah",
    state: "GA",
    postal_code: "31401",
    phone: "9125550123",
    contact_email: "identity@example.com",
  })
  const payer = await api.request<{ id: string }>("POST", "/api/payers", {
    name,
    payer_id: "STEDI",
  })
  await page.goto(PAYERS)
  await expect(page.getByText(name)).toBeVisible()

  // Ask to be credentialed and finish setup — the milestone under test.
  await api.request("PUT", "/api/users/me/preferences", {
    billing_setup_state: ["self_pay"],
    billing_setup_wants_credentialing: true,
    billing_setup_step: "record",
  })
  await page.goto("/dashboard/billing/setup")
  await expect(page.getByRole("heading", { name: /facts every payer will ask/i })).toBeVisible()

  // Nothing was filed with anybody.
  const after = await api.get<{ data: { transaction_type: string }[] }>(
    `/api/payers/${payer.id}/enrollments`,
  )
  expect(after.data.map((r) => r.transaction_type)).not.toContain("835")

  // CONTROL, and the reason the assertion above is worth anything. A payer
  // nobody has enrolled with has no 835 either way, so "none after
  // credentialing" would read exactly the same if credentialing HAD filed one
  // and this read-back were blind. Asking for remittances deliberately proves
  // the instrument reports an 835 when there is one to report.
  await api.request("PATCH", `/api/payers/${payer.id}`, { enroll_remittance: true })
  await api.request("POST", `/api/payers/${payer.id}/enrollments`, {})

  const filed = await api.get<{ data: { transaction_type: string }[] }>(
    `/api/payers/${payer.id}/enrollments`,
  )
  expect(filed.data.map((r) => r.transaction_type)).toContain("835")
})
