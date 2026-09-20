// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The returning patient's half of the portal: signing out, getting back in
 * without a clinician, and the profile.
 *
 * What only a browser against the real stack can prove, and why each one is
 * here rather than in the far faster unit suites:
 *
 *   1. **Sign-out revokes server-side.** The component suite proves the
 *      button clears local storage; only this proves the token stops
 *      working afterwards, which is the part that matters on a shared
 *      computer.
 *   2. **Recovery really sends.** The mint path, the mail server and the
 *      text-message gateway are all real here, and the link that comes out
 *      redeems. A route test can prove a delivery double was called; it
 *      cannot prove the link works.
 *   3. **Recovery is not an oracle**, across a real HTTP boundary: a known
 *      address, an unknown one and a patient whose access was withdrawn all
 *      answer identically.
 *   4. **The profile response carries no staff-authored column** on the
 *      wire, against a chart seeded with one.
 *   5. **The whole thing works at a phone's width, by keyboard**, with no
 *      horizontal scroll — which is the only place that can be checked.
 *
 * Deliberately not here: the uniform-202 matrix in full and the step-up
 * refusals, which cost milliseconds in backend/tests/test_portal_*.py; and
 * the capability intersection, which is a unit-level fact about a route
 * table.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"
import { BACKEND_URL } from "../fixtures/stack"

const REDEEM_PATH = "/api/patient/auth/redeem"
const LOGOUT_PATH = "/api/patient/auth/logout"
const PROFILE_PATH = "/api/patient/profile"
const PATIENT_ROUTE = "/api/patient/intake/form"

/** A phone, so the layout assertions mean something. */
const PHONE_VIEWPORT = { width: 360, height: 740 }

let sequence = 0

/** A fresh address and number per invitation, so one test never reads another's. */
function contactDetails(): { email: string; phone: string } {
  const stamp = `${Date.now().toString(36)}${(sequence++).toString(36)}`
  return {
    email: `portal-acct-${stamp}@example.com`,
    phone: `+1500556${String(sequence).padStart(4, "0")}`,
  }
}

interface SignedInPatient {
  patientId: string
  email: string
  phone: string
  slug: string
  sessionToken: string
}

/**
 * Invite a patient, redeem both factors, and hand back a live session.
 *
 * Both factors are read from the stand-in their channel is wired to — the
 * link out of the mail server, the code out of the text-message gateway.
 * Real sends through the real service; only the last hop is a fake.
 */
async function signInAPatient(
  api: ApiClient,
  request: import("@playwright/test").APIRequestContext,
): Promise<SignedInPatient> {
  const { email, phone } = contactDetails()
  const patient = await givePatient(api, { email, phone })
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")

  await api.post(`/api/patients/${patient.id}/portal-invite`)
  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  const otp = stepUpCode(await sms.waitFor(phone))

  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token, otp },
  })
  expect(redeemed.status(), "the invitation redeems").toBe(200)

  return {
    patientId: patient.id,
    email,
    phone,
    slug,
    sessionToken: (await redeemed.json()).session_token as string,
  }
}

test("signing out stops the token working @portal", async ({ api, request }) => {
  const patient = await signInAPatient(api, request)
  const headers = { Authorization: `Bearer ${patient.sessionToken}` }

  const before = await request.get(`${BACKEND_URL}${PATIENT_ROUTE}`, { headers })
  expect(before.status(), "the session opens the patient's own route").toBe(200)

  const out = await request.post(`${BACKEND_URL}${LOGOUT_PATH}`, { headers })
  expect(out.status()).toBe(200)
  expect((await out.json()).sessions_revoked).toBe(1)

  // The part a cleared browser cannot give you: the credential is dead on
  // the server, so whoever uses this machine next holds nothing.
  const after = await request.get(`${BACKEND_URL}${PATIENT_ROUTE}`, {
    headers,
    failOnStatusCode: false,
  })
  expect(after.status(), "the revoked session is refused").toBe(401)
})

test("recovery sends a link that really signs the patient in @portal", async ({
  api,
  request,
  page,
}) => {
  const patient = await signInAPatient(api, request)

  // Ask for a new link as somebody who has lost theirs, with no session.
  const asked = await request.post(
    `${BACKEND_URL}/api/portal/practices/${patient.slug}/recover`,
    { data: { email: patient.email } },
  )
  expect(asked.status(), "recovery always accepts").toBe(202)

  // Both factors arrive on the patient's own channels, not in the response.
  expect(await asked.text()).not.toContain("invite")
  const link = firstLink(await mail.waitFor(patient.email))
  const otp = stepUpCode(await sms.waitFor(patient.phone))

  // The recovery link has the same shape the clinician's invite produces —
  // it names the practice in the path and carries the credential in the
  // fragment, which is never sent to a server.
  expect(link).toContain(`/portal/${patient.slug}`)
  expect(link).toContain("#invite=")

  await page.goto(link)
  await page.getByTestId("portal-shell-otp-input").fill(otp)
  await page.getByTestId("portal-shell-otp-submit").click()

  await expect(page.getByTestId("portal-shell-active")).toBeVisible()
  expect(page.url(), "the address bar holds no credential").not.toContain("invite")
})

test("recovery answers the same for a stranger as for a patient @portal", async ({
  api,
  request,
}) => {
  const patient = await signInAPatient(api, request)

  const known = await request.post(
    `${BACKEND_URL}/api/portal/practices/${patient.slug}/recover`,
    { data: { email: patient.email }, failOnStatusCode: false },
  )
  const unknown = await request.post(
    `${BACKEND_URL}/api/portal/practices/${patient.slug}/recover`,
    { data: { email: `nobody-${Date.now()}@example.com` }, failOnStatusCode: false },
  )

  // Whether an address is on a therapy practice's patient list is not
  // something a stranger gets to test for.
  expect(known.status()).toBe(unknown.status())
  expect(await known.text()).toBe(await unknown.text())
})

test("a patient whose access was withdrawn gets nothing back @portal", async ({
  api,
  request,
}) => {
  const patient = await signInAPatient(api, request)

  // The clinician's kill switch: every session revoked, every invitation
  // burned.
  await api.delete(`/api/patients/${patient.patientId}/portal-access`)

  const asked = await request.post(
    `${BACKEND_URL}/api/portal/practices/${patient.slug}/recover`,
    { data: { email: patient.email } },
  )
  expect(asked.status(), "the answer is the same one everybody gets").toBe(202)

  // And the session really is gone — recovery did not undo the revoke.
  const after = await request.get(`${BACKEND_URL}${PATIENT_ROUTE}`, {
    headers: { Authorization: `Bearer ${patient.sessionToken}` },
    failOnStatusCode: false,
  })
  expect(after.status(), "only a clinician re-invite restores access").toBe(401)
})

test("the profile carries the patient's own details and no staff notes @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = contactDetails()
  const patient = await givePatient(api, { email, phone })
  const diagnosis = "F41.1 seeded for the portal profile e2e"
  await api.put(`/api/patients/${patient.id}`, { diagnosis, sliding_scale_note: "60 a session" })

  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
  expect(slug).toBeTruthy()
  await api.post(`/api/patients/${patient.id}/portal-invite`)
  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token, otp: stepUpCode(await sms.waitFor(phone)) },
  })
  const headers = { Authorization: `Bearer ${(await redeemed.json()).session_token}` }

  const profile = await request.get(`${BACKEND_URL}${PROFILE_PATH}`, { headers })
  expect(profile.status()).toBe(200)

  const body = await profile.text()
  // On the wire, not just in the model: a serializer that widened by
  // default would satisfy every registry assertion and fail here.
  expect(body).not.toContain(diagnosis)
  expect(body).not.toContain("60 a session")
  expect(JSON.parse(body).email).toBe(email)

  // The write half, and the refusal beside it.
  const patched = await request.patch(`${BACKEND_URL}${PROFILE_PATH}`, {
    headers,
    data: { city: "Brooklyn" },
  })
  expect((await patched.json()).city).toBe("Brooklyn")

  const identity = await request.patch(`${BACKEND_URL}${PROFILE_PATH}`, {
    headers,
    data: { first_name: "Somebody Else" },
    failOnStatusCode: false,
  })
  expect(identity.status(), "identity is the clinician's to change").toBe(422)
})

test("the portal works at a phone's width, by keyboard @portal", async ({ api, page }) => {
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
  await page.setViewportSize(PHONE_VIEWPORT)

  await page.goto(`/portal/${slug}`)
  await expect(page.getByTestId("portal-shell-no-session")).toBeVisible()

  const shellOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(shellOverflow, "the shell does not scroll sideways on a phone").toBeLessThanOrEqual(0)

  // Reach recovery the way somebody without a mouse does.
  await page.getByTestId("portal-shell-recover-link").focus()
  await page.keyboard.press("Enter")

  const field = page.getByTestId("portal-recover-email")
  await expect(field).toBeVisible()
  await field.focus()
  await page.keyboard.type("nobody@example.com")
  await page.keyboard.press("Tab")
  await expect(page.getByTestId("portal-recover-submit")).toBeFocused()
  await page.keyboard.press("Enter")

  // One conditional sentence, announced, for every address.
  await expect(page.getByTestId("portal-recover-sent")).toBeVisible()
  await expect(page.getByRole("status")).toContainText("If we find a portal account")

  const recoverOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(recoverOverflow, "the recovery page does not scroll sideways").toBeLessThanOrEqual(0)
})
