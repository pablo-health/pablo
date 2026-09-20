// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A patient is sent the practice's intake form, fills it in, and hands it back.
 *
 * Every step of that crosses a boundary nothing else in the suite crosses
 * together: a clinician sends a published version, an invitation goes out over
 * two channels to two different stand-ins, the patient redeems it into a
 * session of their own, saves answers one question at a time under that
 * session, and submits — at which point the screeners on the form are scored
 * onto the chart the clinician reads.
 *
 * It is driven through the API rather than the browser, deliberately and for
 * now. The portal's own screens for answering a form have not shipped yet, so
 * there is nothing to click; what is under test here is the wiring — that the
 * two principals are really separate, that the invitation really arrives, and
 * that a submission really lands on the chart. When the portal screens land,
 * the answering half of this belongs in a spec that drives them, and this one
 * keeps the parts a browser cannot reach.
 *
 * The two factors come from two stand-ins because they travel two channels:
 * `mail.ts` reads the magic link out of the invitation email, `sms.ts` reads
 * the step-up code out of the text. Their headers walk the sign-in in full.
 */

import { expect, test } from "../fixtures/auth"
import { ApiClient, ApiError } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { sms, stepUpCode } from "../fixtures/sms"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

interface IntakeVersion {
  id: string
  version: number
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  name: string
  archived_at: string | null
  versions: IntakeVersion[]
}

interface Assignment {
  id: string
  version_id: string
  status: string
  receipt_code: string | null
  progress: { complete: boolean; missing: string[] }
}

interface AssignmentDetail extends Assignment {
  items: { id: string; key: string; item_type: string; required: boolean }[]
}

interface Receipt {
  assignment_id: string
  receipt_code: string
  measures: { instrument: string; total_score: number | null; severity: string | null }[]
}

/** The answer each question on the form every practice starts with takes. */
const ANSWERS: Record<string, Record<string, unknown>> = {
  demographics: { name_confirmed: true, dob_confirmed: true },
  reason: { text: "Panic before every shift, for about two months." },
  phq9: { item_scores: Object.fromEntries(Array.from({ length: 9 }, (_, i) => [`${i + 1}`, 1])) },
  gad7: { item_scores: Object.fromEntries(Array.from({ length: 7 }, (_, i) => [`${i + 1}`, 2])) },
}

/** The published version of the form a fresh practice is seeded with. */
async function defaultIntakeVersion(api: ApiClient): Promise<string> {
  const templates = await api.get<IntakeTemplate[]>("/api/intake/templates")
  const intake = templates.find((t) => t.name === "Intake" && t.archived_at === null)
  if (intake === undefined) {
    throw new Error("no Intake form on this practice; every schema is seeded with one")
  }
  const published = intake.versions.filter((v) => v.published_at !== null)
  expect(published.length, "the seeded version ships published").toBeGreaterThan(0)
  return published[0].id
}

/**
 * Sign the patient in the way the product does: invite, read both factors
 * from their own channel, redeem. Returns a bearer client for that patient.
 *
 * The token rides in the link's fragment (`/portal/{slug}#invite=…`), which is
 * what keeps it out of request logs and referrers on the way to the page.
 */
async function signInAsPatient(
  api: ApiClient,
  patientId: string,
  email: string,
  phone: string,
): Promise<ApiClient> {
  await api.post(`/api/patients/${patientId}/portal-invite`)

  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  expect(token, `no invite token in ${link}`).toBeTruthy()

  const otp = stepUpCode(await sms.waitFor(phone))
  const response = await fetch(`${BACKEND_URL}/api/patient/auth/redeem`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, otp }),
  })
  expect(response.status, "redeeming the invitation mints a patient session").toBe(200)
  const { session_token } = (await response.json()) as { session_token: string }
  return new ApiClient(session_token)
}

test.describe("intake: sent, filled in, handed back", () => {
  test("a patient submits the practice's form and it lands on the chart", async ({ api }) => {
    const suffix = Date.now().toString(36)
    const email = `intake-${suffix}@example.com`
    const phone = `+1555${`${Date.now()}`.slice(-7)}`

    const patient = await givePatient(api, { email, phone, date_of_birth: "1990-03-14" })
    const versionId = await defaultIntakeVersion(api)

    // --- the clinician asks -------------------------------------------------
    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: versionId },
    )
    expect(assigned.status).toBe("assigned")
    expect(assigned.progress.complete).toBe(false)

    // --- the patient signs in and answers -----------------------------------
    const portal = await signInAsPatient(api, patient.id, email, phone)

    const mine = await portal.get<Assignment[]>("/api/patient/intake/assignments")
    expect(mine.map((row) => row.id)).toEqual([assigned.id])

    const detail = await portal.get<AssignmentDetail>(
      `/api/patient/intake/assignments/${assigned.id}`,
    )
    for (const item of detail.items) {
      const answer = ANSWERS[item.key]
      expect(answer, `no answer written for a '${item.key}' question`).toBeDefined()
      await portal.put(
        `/api/patient/intake/assignments/${assigned.id}/items/${item.id}`,
        { value: answer },
      )
    }

    // --- and hands it in ----------------------------------------------------
    const receipt = await portal.post<Receipt>(
      `/api/patient/intake/assignments/${assigned.id}/submit`,
    )
    expect(receipt.assignment_id).toBe(assigned.id)
    // Eight characters the patient can read down a phone line.
    expect(receipt.receipt_code).toMatch(/^[2-9A-HJ-NP-TV-Z]{8}$/)
    expect(receipt.measures.map((m) => m.instrument)).toEqual(["phq9", "gad7"])

    // --- the chart has it ---------------------------------------------------
    const measures = await api.get<{ data: { instrument: string; total_score: number }[] }>(
      `/api/patients/${patient.id}/outcome-measures`,
    )
    expect(measures.data.map((m) => m.instrument).sort()).toEqual(["gad7", "phq9"])
    expect(measures.data.find((m) => m.instrument === "phq9")?.total_score).toBe(9)
    expect(measures.data.find((m) => m.instrument === "gad7")?.total_score).toBe(14)

    const onChart = await api.get<Assignment>(
      `/api/patients/${patient.id}/intake-assignments/${assigned.id}`,
    )
    expect(onChart.status).toBe("submitted")
    expect(onChart.receipt_code).toBe(receipt.receipt_code)

    // --- and it cannot be handed in twice -----------------------------------
    const again = await portal
      .post(`/api/patient/intake/assignments/${assigned.id}/submit`)
      .then(() => null)
      .catch((error: unknown) => error)
    expect(again, "a second submit must not mint a second receipt").toBeInstanceOf(ApiError)
    expect((again as ApiError).status).toBe(409)
  })
})
