// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient portal sign-in, and what it refuses.
 *
 * The portal adds a patient principal, which is a new attack surface, so the
 * negatives are as load-bearing as the happy path. These are the ones an
 * end-to-end test is uniquely positioned to prove: they cross the real HTTP
 * boundary, the real database, and — for the last two — the boundary between
 * the patient principal and the clinician one.
 *
 * **Where the two factors come from.** The product never returns the
 * invitation token: it goes to the patient's email address and nowhere else,
 * and the step-up code only ever exists in a text message. So this spec reads
 * each from the stand-in its channel is wired to on this stack — the link out
 * of the mail server, the code out of the text-message gateway. Both are real
 * sends through the real service; only the last hop is a fake. The challenge
 * row, the single-use flag, the attempt cap and the hashed code are the
 * production ones.
 *
 * Deliberately not here: token expiry, which an end-to-end test cannot wait
 * out and the unit suite asserts against an injected clock; and the
 * exhaustive uniform-401 matrix, which costs milliseconds in
 * backend/tests/test_portal_auth_routes.py and a round trip here.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"
import { BACKEND_URL } from "../fixtures/stack"

const REDEEM_PATH = "/api/patient/auth/redeem"
const PATIENT_ROUTE = "/api/patient/intake/form"
const CLINICIAN_ROUTE = "/api/patients"

let sequence = 0

/** A fresh address and number per invitation, so one test never reads another's. */
function contactDetails(): { email: string; phone: string } {
  const stamp = `${Date.now().toString(36)}${(sequence++).toString(36)}`
  return { email: `portal-${stamp}@example.com`, phone: `+1500555${String(sequence).padStart(4, "0")}` }
}

interface Invitation {
  patientId: string
  /** The whole link, exactly as the email carried it. */
  link: string
  token: string
  otp: string
}

/**
 * Invite a patient, then read back both factors the way the patient does.
 *
 * The link arrives with its token in the URL fragment — a fragment is never
 * sent to a server, which is why the token travels in one.
 */
async function givePortalInvitation(api: ApiClient): Promise<Invitation> {
  const { email, phone } = contactDetails()
  const patient = await givePatient(api, { email, phone })

  await api.post(`/api/patients/${patient.id}/portal-invite`)

  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  expect(token, `the invitation email carries a token: ${link}`).toBeTruthy()

  return {
    patientId: patient.id,
    link,
    token: token as string,
    otp: stepUpCode(await sms.waitFor(phone)),
  }
}

test("a single-use invitation cannot be redeemed twice @portal", async ({ api, request }) => {
  const invitation = await givePortalInvitation(api)

  const first = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: invitation.otp },
  })
  expect(first.status(), "first redemption mints a session").toBe(200)
  expect((await first.json()).session_token).toBeTruthy()

  const replay = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: invitation.otp },
    failOnStatusCode: false,
  })
  expect(replay.status(), "the same invitation cannot be spent twice").toBe(401)
})

test("a leaked link without the step-up code mints nothing @portal", async ({ api, request }) => {
  const invitation = await givePortalInvitation(api)

  // The §164.312(d) point: possession of the link is one factor.
  const wrongCode = invitation.otp === "000000" ? "111111" : "000000"
  const wrong = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: wrongCode },
    failOnStatusCode: false,
  })
  expect(wrong.status(), "a wrong step-up code is refused").toBe(401)
  expect(await wrong.text()).not.toContain("session_token")

  // The link alone, with no code at all, is a malformed request rather than a
  // failed authentication — it never reaches the token.
  const missing = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token },
    failOnStatusCode: false,
  })
  expect([400, 401, 422], `no step-up is refused (got ${missing.status()})`).toContain(
    missing.status(),
  )
  expect(await missing.text()).not.toContain("session_token")

  // And the patient, who has both factors, is not locked out by someone
  // else's wrong guess — the attempt cap is several, not one.
  const legitimate = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: invitation.otp },
  })
  expect((await legitimate.json()).session_token, "the real code still redeems").toBeTruthy()
})

test("a patient session opens patient routes and no clinician one @portal", async ({
  api,
  request,
}) => {
  const invitation = await givePortalInvitation(api)
  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: invitation.otp },
  })
  const sessionToken = (await redeemed.json()).session_token as string
  const headers = { Authorization: `Bearer ${sessionToken}` }

  // The session is real: it opens the patient's own surface.
  const own = await request.get(`${BACKEND_URL}${PATIENT_ROUTE}`, { headers })
  expect(own.status(), "the patient's own route accepts the session").toBe(200)

  // And principal separation, end to end. The session token is signed by the
  // engine's own key; every clinician verifier expects a provider-issued one,
  // so the refusal is structural rather than a check someone remembered to
  // write — but this is the only place that claim is proven across HTTP.
  const roster = await request.get(`${BACKEND_URL}${CLINICIAN_ROUTE}`, {
    headers,
    failOnStatusCode: false,
  })
  expect(
    [401, 403],
    `a patient session is refused the clinician roster (got ${roster.status()})`,
  ).toContain(roster.status())
})

test("the invite route never returns a credential @portal", async ({ api }) => {
  const { email, phone } = contactDetails()
  const patient = await givePatient(api, { email, phone })

  // The clinician acknowledges that an invitation went out and learns nothing
  // else. A screenshot, a support ticket or a browser history entry must not
  // be a credential, so this is asserted against the whole body: any future
  // field carrying either factor fails here.
  const accepted = await api.post<Record<string, unknown>>(
    `/api/patients/${patient.id}/portal-invite`,
  )

  const body = JSON.stringify(accepted)
  expect(body).not.toContain("token")
  expect(body).not.toContain("otp")

  // What the clinician may see: that this patient now has one in flight.
  const access = await api.get<{ invite_outstanding: boolean }>(
    `/api/patients/${patient.id}/portal-access`,
  )
  expect(access.invite_outstanding).toBe(true)
})

/**
 * The whole sign-in, as the patient performs it: open the link the email
 * carried, type the code the text carried, and arrive in the portal.
 *
 * The other tests in this file post to the redeem route directly, which
 * proves the credential rules but not that the link goes anywhere. This one
 * opens the captured link in a browser and never types a URL of its own.
 */
test("a patient signs in from the link in their email @portal", async ({ api, page }) => {
  const invitation = await givePortalInvitation(api)

  await page.goto(invitation.link)

  await page.getByTestId("portal-shell-otp-input").fill(invitation.otp)
  await page.getByTestId("portal-shell-otp-submit").click()

  await expect(page.getByTestId("portal-shell-active")).toBeVisible()
  await expect(page.getByTestId("portal-shell-practice-name")).toBeVisible()

  // The address bar still names the practice and no longer holds the
  // invitation: a reloaded tab or a shared screen is not a credential.
  expect(page.url()).not.toContain("invite")
  await expect(page).toHaveURL(/\/portal\/[^/#?]+$/)
})
