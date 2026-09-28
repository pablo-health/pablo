// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Refill requests over the real stack: a patient asks from the portal, the
 * prescriber answers from the practice's queue, and the patient sees the
 * answer — without the practice's note on it.
 *
 * Both surfaces are exercised: the patient's own routes
 * (`/api/patient/refills...`) and the clinician's queue and decision
 * (`/api/refill-requests...`). The isolation claims an end-to-end test is
 * placed to make are made here over real HTTP: a second patient cannot see
 * the first one's request, and neither surface accepts the other's principal.
 *
 * The browser tests drive the portal the way a patient does: sign in from
 * the invitation, find the form open because there is nothing else yet,
 * send, see the confirmation and the new row, and — after the prescriber
 * answers — the status, the date it was decided and what to do next.
 *
 * Needs `refills` and `messaging` in the stack's `PORTAL_MODULES`
 * (docker-compose.e2e.yml); the declined row's next-step line points at
 * messaging and is shown only when it is on.
 */

import type { Browser, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { ApiError, signInWithPassword } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  givePortalSession,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL, BASE_URL } from "../fixtures/stack"

/**
 * Open the refills section of a signed-in portal. The one place that knows
 * where refills live in the portal, so a move is a change here only.
 */
async function openRefills(page: Page): Promise<void> {
  await page.getByTestId("portal-shell-nav-refills").click()
  await expect(page.getByTestId("portal-refills")).toBeVisible()
}

const PATIENT_REFILLS = "/api/patient/refills"
const CLINICIAN_REFILLS = "/api/refill-requests"

/** A date as the portal shows it on a request row. */
function shownDate(value: string | Date): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

interface Medication {
  id: string
  drug_name: string
  dose: string
}

interface PatientRefill {
  id: string
  medication_id: string | null
  medication_text: string
  status: string
  created_at: string
  decided_at: string | null
}

interface ClinicianRefill extends PatientRefill {
  patient_id: string
  patient_name: string | null
  prescriber_note: string | null
}

interface ListResponse<T> {
  data: T[]
  total: number
}

let medicationSequence = 0

/** A medication on the patient's chart, added the way a clinician adds one. */
async function giveMedication(
  api: Parameters<typeof givePatient>[0],
  patientId: string,
  status = "active",
): Promise<Medication> {
  const drugName = `E2E Sertraline ${Date.now().toString(36)}${medicationSequence++}`
  return api.post<Medication>(`/api/patients/${patientId}/medications`, {
    drug_name: drugName,
    dose: "50 mg",
    status,
  })
}

// --- Test 1: ask, answer, see the answer ---------------------------------

test("a patient asks for a refill, the prescriber answers, and the patient sees it @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const active = await giveMedication(api, patient.id)
  const stopped = await giveMedication(api, patient.id, "discontinued")
  const headers = {
    Authorization: `Bearer ${await givePortalSession(api, request, patient.id, email, phone)}`,
  }

  const options = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}/medications`, { headers })
  ).json()) as ListResponse<Medication>
  expect(options.data.map((m) => m.id), "only active medications are offered").toEqual([
    active.id,
  ])
  expect(options.data.map((m) => m.id)).not.toContain(stopped.id)

  const asked = await request.post(`${BACKEND_URL}${PATIENT_REFILLS}`, {
    headers,
    data: { medication_id: active.id, pharmacy_text: "Main St pharmacy", patient_note: "Out Friday" },
  })
  expect(asked.status(), "asking for a refill").toBe(201)
  const refill = (await asked.json()) as PatientRefill
  expect(refill.status).toBe("requested")
  expect(refill.medication_text).toBe(`${active.drug_name} 50 mg`)

  const queue = await api.get<ListResponse<ClinicianRefill>>(CLINICIAN_REFILLS)
  const queued = queue.data.find((row) => row.id === refill.id)
  expect(queued, "the request reaches the prescriber's queue").toBeTruthy()
  expect(queued?.patient_id).toBe(patient.id)

  const decided = await api.post<ClinicianRefill>(`${CLINICIAN_REFILLS}/${refill.id}/decision`, {
    status: "approved",
    prescriber_note: "Sent to Main St",
  })
  expect(decided.status).toBe("approved")
  expect(decided.decided_at).toBeTruthy()

  const pending = await api.get<ListResponse<ClinicianRefill>>(CLINICIAN_REFILLS)
  expect(pending.data.map((row) => row.id), "answered requests leave the queue").not.toContain(
    refill.id,
  )
  const recent = await api.get<ListResponse<ClinicianRefill>>(`${CLINICIAN_REFILLS}?view=recent`)
  expect(recent.data.map((row) => row.id)).toContain(refill.id)

  const mine = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}`, { headers })
  ).json()) as ListResponse<PatientRefill>
  const seen = mine.data.find((row) => row.id === refill.id)
  expect(seen?.status).toBe("approved")
  expect(JSON.stringify(seen), "the practice's note is not the patient's").not.toContain(
    "Sent to Main St",
  )
})

// --- Test 2: one answer only; a typed name works too ----------------------

test("a request typed by name is answered once, and a second answer is refused @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const headers = {
    Authorization: `Bearer ${await givePortalSession(api, request, patient.id, email, phone)}`,
  }

  const asked = await request.post(`${BACKEND_URL}${PATIENT_REFILLS}`, {
    headers,
    data: { medication_text: "Hydroxyzine 25 mg" },
  })
  expect(asked.status()).toBe(201)
  const refill = (await asked.json()) as PatientRefill
  expect(refill.medication_id).toBeNull()

  await api.post(`${CLINICIAN_REFILLS}/${refill.id}/decision`, { status: "needs_visit" })

  const second = api.post(`${CLINICIAN_REFILLS}/${refill.id}/decision`, { status: "approved" })
  await expect(second).rejects.toBeInstanceOf(ApiError)
  await expect(second).rejects.toMatchObject({ status: 409 })

  const detail = await api.get<ClinicianRefill>(`${CLINICIAN_REFILLS}/${refill.id}`)
  expect(detail.status, "the first answer stands").toBe("needs_visit")
})

// --- Test 3: isolation over HTTP, control before every negative -----------

test("one patient's request and medications are invisible to another, and each surface refuses the other's principal @portal", async ({
  api,
  onboardedUser,
  request,
}) => {
  const a = givePortalContactDetails()
  const b = givePortalContactDetails()
  const patientA = await givePatient(api, { email: a.email, phone: a.phone })
  const patientB = await givePatient(api, { email: b.email, phone: b.phone })
  const medicationA = await giveMedication(api, patientA.id)
  const headersA = {
    Authorization: `Bearer ${await givePortalSession(api, request, patientA.id, a.email, a.phone)}`,
  }
  const headersB = {
    Authorization: `Bearer ${await givePortalSession(api, request, patientB.id, b.email, b.phone)}`,
  }

  const asked = await request.post(`${BACKEND_URL}${PATIENT_REFILLS}`, {
    headers: headersA,
    data: { medication_id: medicationA.id },
  })
  const refillId = ((await asked.json()) as PatientRefill).id

  const listA = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}`, { headers: headersA })
  ).json()) as ListResponse<PatientRefill>
  expect(listA.data.map((row) => row.id), "control: A sees their own").toContain(refillId)

  const listB = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}`, { headers: headersB })
  ).json()) as ListResponse<PatientRefill>
  expect(listB.data.map((row) => row.id)).not.toContain(refillId)

  const medsB = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}/medications`, { headers: headersB })
  ).json()) as ListResponse<Medication>
  expect(medsB.data.map((m) => m.id)).not.toContain(medicationA.id)

  const borrowed = await request.post(`${BACKEND_URL}${PATIENT_REFILLS}`, {
    headers: headersB,
    data: { medication_id: medicationA.id },
  })
  expect(borrowed.status(), "B cannot ask for A's medication").toBe(404)

  // A patient's session is not a clinician's, and the reverse.
  const patientOnClinician = await request.get(`${BACKEND_URL}${CLINICIAN_REFILLS}`, {
    headers: headersA,
    failOnStatusCode: false,
  })
  expect([401, 403], "a patient session is refused the clinician queue").toContain(
    patientOnClinician.status(),
  )
  // The one write on the clinician surface: neither patient can answer a
  // request, their own included, and the request is untouched afterwards.
  for (const headers of [headersA, headersB]) {
    const decided = await request.post(`${BACKEND_URL}${CLINICIAN_REFILLS}/${refillId}/decision`, {
      headers,
      data: { status: "approved" },
      failOnStatusCode: false,
    })
    expect([401, 403], "a patient session cannot decide a refill").toContain(decided.status())
  }
  const afterAttempts = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}`, { headers: headersA })
  ).json()) as ListResponse<PatientRefill>
  expect(afterAttempts.data.find((row) => row.id === refillId)?.status).toBe("requested")
  const clinicianIdToken = await signInWithPassword(onboardedUser.email, onboardedUser.password)
  const clinicianOnPatient = await request.get(`${BACKEND_URL}${PATIENT_REFILLS}`, {
    headers: { Authorization: `Bearer ${clinicianIdToken}` },
    failOnStatusCode: false,
  })
  expect(clinicianOnPatient.status(), "a clinician bearer resolves to no patient").toBe(401)
})

// --- Test 4: the portal, in a browser -------------------------------------

/**
 * Sign a new patient into the portal, open refills with nothing asked for
 * yet, and send one request from the browser. Checks what the patient
 * sees on the way — the form open because there is nothing else, then
 * the confirmation, the form closed behind its button, and the new row
 * marked as received today — and returns the stored request.
 */
async function askFromThePortal(
  api: Parameters<typeof givePatient>[0],
  page: Page,
): Promise<{ refill: ClinicianRefill; drugName: string }> {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const medication = await giveMedication(api, patient.id)
  const invitation = await givePortalInvitation(api, patient.id, email, phone)

  await signInToPortal(page, invitation)
  await openRefills(page)

  // Nothing asked for yet: the form is the whole page, open without a press.
  await expect(page.getByTestId("portal-refills-list-empty")).toBeVisible()
  await expect(page.getByTestId("portal-refills-form")).toBeVisible()
  await expect(page.getByTestId("portal-refills-open-form")).toHaveCount(0)
  await expect(page.getByTestId("portal-refills-crisis-line")).toContainText("988")

  await expect(page.getByTestId("portal-refills-submit")).toBeDisabled()
  await page.getByTestId(`portal-refills-medication-${medication.id}`).check()
  await page.getByTestId("portal-refills-pharmacy").fill("Main St pharmacy")
  await page.getByTestId("portal-refills-submit").click()

  await expect(page.getByTestId("portal-refills-sent")).toHaveText(
    /^Sent to .+\. You'll see the answer here\.$/,
  )
  await expect(page.getByTestId("portal-refills-form")).toHaveCount(0)
  await expect(page.getByTestId("portal-refills-open-form")).toBeVisible()

  const queue = await api.get<ListResponse<ClinicianRefill>>(CLINICIAN_REFILLS)
  const queued = queue.data.find((row) => row.patient_id === patient.id)
  expect(queued, "the browser's request reached the prescriber's queue").toBeTruthy()
  const refill = queued as ClinicianRefill

  const row = page.getByTestId(`portal-refills-request-${refill.id}`)
  await expect(row).toHaveAttribute("data-highlighted", "true")
  await expect(row).toContainText(medication.drug_name)
  await expect(page.getByTestId(`portal-refills-status-${refill.id}`)).toHaveText("Received")
  await expect(page.getByTestId(`portal-refills-date-${refill.id}`)).toHaveText(
    shownDate(new Date()),
  )
  await expect(page.getByTestId(`portal-refills-next-${refill.id}`)).toHaveText(
    "Your prescriber will look at this.",
  )

  // With a request on file, a fresh visit shows the list and keeps the
  // form behind its button until it is asked for.
  await page.reload()
  await openRefills(page)
  await expect(page.getByTestId(`portal-refills-request-${refill.id}`)).toBeVisible()
  await expect(page.getByTestId("portal-refills-form")).toHaveCount(0)
  await page.getByTestId("portal-refills-open-form").click()
  await expect(page.getByTestId("portal-refills-form")).toBeVisible()
  await page.getByTestId("portal-refills-cancel").click()
  await expect(page.getByTestId("portal-refills-form")).toHaveCount(0)

  return { refill, drugName: medication.drug_name }
}

/** Reload the portal and check the row reads as a decided request. */
async function expectAnswered(
  page: Page,
  refillId: string,
  decidedAt: string,
  label: string,
  nextStep: string,
): Promise<void> {
  await page.reload()
  await openRefills(page)
  await expect(page.getByTestId(`portal-refills-status-${refillId}`)).toHaveText(label)
  await expect(page.getByTestId(`portal-refills-date-${refillId}`)).toHaveText(
    shownDate(decidedAt),
  )
  await expect(page.getByTestId(`portal-refills-next-${refillId}`)).toHaveText(nextStep)
  await expect(page.getByTestId("portal-refills")).not.toContainText("private to the practice")
}

/** A browser with nothing saved in it, for the patient beside a signed-in clinician. */
async function patientPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ baseURL: BASE_URL, storageState: undefined })
  return context.newPage()
}

test("a patient asks from the portal, the prescriber approves on the Refills page, and the patient sees what to do next @portal", async ({
  api,
  browser,
  signedInPage: clinician,
}) => {
  const patient = await patientPage(browser)
  try {
    const { refill, drugName } = await askFromThePortal(api, patient)

    await clinician.goto("/dashboard/refills")
    const row = clinician.getByTestId(`refill-row-${refill.id}`)
    await expect(row).toContainText(drugName)
    await row.getByTestId("refill-decide-approved").click()
    await row.getByTestId("refill-note").fill("private to the practice")
    await row.getByTestId("refill-confirm-submit").click()
    await expect(row, "an answered request leaves the queue").toHaveCount(0)

    const decided = await api.get<ClinicianRefill>(`${CLINICIAN_REFILLS}/${refill.id}`)
    expect(decided.status).toBe("approved")
    expect(decided.decided_at, "the decision is timestamped").toBeTruthy()

    await expectAnswered(
      patient,
      refill.id,
      decided.decided_at as string,
      "Sent to your pharmacy",
      "Check with your pharmacy before you go.",
    )
  } finally {
    await patient.context().close()
  }
})

// The other two answers, each checking what the patient reads when it
// arrives. The declined line points at messaging, which this stack serves.
const OTHER_DECISIONS = [
  {
    status: "needs_visit",
    label: "Let's talk at your next visit",
    next: "Bring this up at your next appointment.",
  },
  {
    status: "declined",
    label: "Not refilled",
    next: "Message your practice if you have questions.",
  },
] as const

for (const { status, label, next } of OTHER_DECISIONS) {
  test(`a patient asks from the portal and sees "${label}" when the answer is ${status} @portal`, async ({
    api,
    page,
  }) => {
    const { refill } = await askFromThePortal(api, page)

    const decided = await api.post<ClinicianRefill>(`${CLINICIAN_REFILLS}/${refill.id}/decision`, {
      status,
      prescriber_note: "private to the practice",
    })

    await expectAnswered(page, refill.id, decided.decided_at as string, label, next)
  })
}

// --- Test: the prescriber answers from the clinician page -----------------

test("a prescriber answers a portal refill request from the Refills page @portal", async ({
  api,
  request,
  signedInPage: page,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const medication = await giveMedication(api, patient.id)
  const headers = {
    Authorization: `Bearer ${await givePortalSession(api, request, patient.id, email, phone)}`,
  }
  const asked = await request.post(`${BACKEND_URL}${PATIENT_REFILLS}`, {
    headers,
    data: { medication_id: medication.id, patient_note: "Out on Friday" },
  })
  const refillId = ((await asked.json()) as PatientRefill).id

  await page.goto("/dashboard")
  await page.getByRole("link", { name: "Refills" }).click()
  await expect(page).toHaveURL(/\/dashboard\/refills$/)

  const row = page.getByTestId(`refill-row-${refillId}`)
  await expect(row).toContainText(medication.drug_name)
  await expect(row).toContainText("Out on Friday")

  await row.getByTestId("refill-decide-approved").click()
  await row.getByTestId("refill-note").fill("private to the practice")
  await row.getByTestId("refill-confirm-submit").click()

  await expect(row, "an answered request leaves the queue").toHaveCount(0)
  await page.getByTestId("refill-recent").locator("summary").click()
  await expect(page.getByTestId(`refill-recent-${refillId}`)).toContainText("Sent to pharmacy")

  const mine = (await (
    await request.get(`${BACKEND_URL}${PATIENT_REFILLS}`, { headers })
  ).json()) as ListResponse<PatientRefill>
  const seen = mine.data.find((entry) => entry.id === refillId)
  expect(seen?.status).toBe("approved")
  expect(JSON.stringify(seen)).not.toContain("private to the practice")
})
