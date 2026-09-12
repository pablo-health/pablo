// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The credentialing intake, against the whole stack.
 *
 * What only a real run can show: that the question set narrows and the answers
 * land for a freshly-onboarded practice, against a provisioned tenant schema
 * with row-level security armed. The unit suite proves the shape; this proves
 * a real clinician with an empty record gets a usable surface rather than a
 * 500 — which matters more than usual here, because the billing page now asks
 * this endpoint on every load.
 */

import { test, expect } from "../fixtures/auth"

interface IntakeField {
  key: string
  tier: string
  kind: string
  required: boolean
  source: string | null
  answered: boolean
}

interface TierProgress {
  tier: string
  answered: number
  required: number
  complete: boolean
}

interface IntakeSurface {
  supervised: boolean
  prescriber: boolean
  claims_ready: boolean
  progress: TierProgress[]
  fields: IntakeField[]
}

interface Confirmation {
  field_key: string
  confirmed: boolean
  correction: string | null
}

const INTAKE = "/api/credentialing/intake"

test.describe("credentialing intake", () => {
  test("a real practice gets a whole question set back", async ({ api }) => {
    const intake = await api.get<IntakeSurface>(INTAKE)

    expect(intake.fields.length).toBeGreaterThan(0)
    // Per tier, never one figure — finishing the claims-ready tier and stopping
    // is a complete outcome, and a single number would render it as half done.
    expect(intake.progress.map((t) => t.tier)).toEqual([
      "tier_0_confirm",
      "tier_1_claims_ready",
      "tier_2_credentialing",
    ])
    // Counts are NOT asserted against zero: the account is worker-scoped and
    // shared across the whole run, and onboarding itself fills some of this in.
    // That a fact entered elsewhere already counts is the point, not a flaw.
    expect(intake.progress.every((t) => t.required > 0)).toBe(true)
  })

  test("nothing in the confirm tier is an empty box awaiting typing", async ({ api }) => {
    const intake = await api.get<IntakeSurface>(INTAKE)

    const confirmTier = intake.fields.filter((f) => f.tier === "tier_0_confirm")
    expect(confirmTier.length).toBeGreaterThan(0)
    expect(confirmTier.every((f) => f.source !== null)).toBe(true)
  })

  test("the supervision fork changes what is asked", async ({ api }) => {
    const independent = await api.get<IntakeSurface>(`${INTAKE}?supervised=false`)
    const supervised = await api.get<IntakeSurface>(`${INTAKE}?supervised=true`)

    expect(independent.supervised).toBe(false)
    expect(supervised.supervised).toBe(true)
    const keys = (s: IntakeSurface) => s.fields.map((f) => f.key)
    expect(keys(supervised)).toContain("supervisor")
    expect(keys(independent)).not.toContain("supervisor")
  })

  test("an answer is remembered, and the branch follows it", async ({ api }) => {
    await api.patch(`${INTAKE}/answers`, { supervision_status: "supervised" })

    const intake = await api.get<IntakeSurface>(INTAKE)

    expect(intake.supervised).toBe(true)
    expect(intake.fields.map((f) => f.key)).toContain("supervisor")
  })

  test("the CAQH question is answered in the claims-ready tier", async ({ api }) => {
    await api.patch(`${INTAKE}/answers`, { caqh_id: "16273849" })

    const intake = await api.get<IntakeSurface>(INTAKE)
    const caqh = intake.fields.find((f) => f.key === "caqh_id")

    expect(caqh?.tier).toBe("tier_1_claims_ready")
    expect(caqh?.answered).toBe(true)
  })

  test("a confirmation is recorded, and correcting one replaces it", async ({ api }) => {
    await api.put(`${INTAKE}/confirmations/npi_number`, {
      source: "nppes",
      confirmed: true,
      presented_value: "1999999984",
    })
    await api.put(`${INTAKE}/confirmations/npi_number`, {
      source: "nppes",
      confirmed: false,
      presented_value: "1999999984",
      correction: "1234567893",
    })

    const confirmations = await api.get<Confirmation[]>(`${INTAKE}/confirmations`)
    const npi = confirmations.filter((c) => c.field_key === "npi_number")

    expect(npi).toHaveLength(1)
    expect(npi[0].confirmed).toBe(false)
    expect(npi[0].correction).toBe("1234567893")
  })

  test("confirming answers a question that has nowhere else to live", async ({ api }) => {
    await api.put(`${INTAKE}/confirmations/exclusion_clearance`, {
      source: "leie_sam",
      confirmed: true,
      presented_value: "true",
    })

    const intake = await api.get<IntakeSurface>(INTAKE)
    const cleared = intake.fields.find((f) => f.key === "exclusion_clearance")

    expect(cleared?.answered).toBe(true)
  })

  test("the banking answers do not block the claims-ready finish", async ({ api }) => {
    // No payer has asked for proof of an account at intake time, and EFT
    // enrolment happens after contracting — so neither may be required here.
    const intake = await api.get<IntakeSurface>(INTAKE)
    const byKey = new Map(intake.fields.map((f) => [f.key, f]))

    expect(byKey.get("bank_account")?.required).toBe(false)
    expect(byKey.get("voided_cheque")?.required).toBe(false)
    // The contrast worth keeping: which panels she is on decides whether a
    // session bills as a claim or a superbill, so it stays required.
    expect(byKey.get("payer_participation")?.required).toBe(true)
  })
})
