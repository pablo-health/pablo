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

import type { APIRequestContext, APIResponse } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { askForCodeInPage, requestStepUpCode, signInFromLink } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"
import { BACKEND_URL } from "../fixtures/stack"

const REDEEM_PATH = "/api/patient/auth/redeem"
const REQUEST_CODE_PATH = "/api/patient/auth/request-code"
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
  phone: string
  email: string
}

/**
 * Invite a patient, then read back the link the way the patient does.
 *
 * The link arrives with its token in the URL fragment — a fragment is never
 * sent to a server, which is why the token travels in one. The code does not
 * exist yet: it is texted when the patient asks for it.
 */
async function givePortalInvitation(api: ApiClient): Promise<Invitation> {
  const { email, phone } = contactDetails()
  const patient = await givePatient(api, { email, phone })

  await api.post(`/api/patients/${patient.id}/portal-invite`)

  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  expect(token, `the invitation email carries a token: ${link}`).toBeTruthy()

  return { patientId: patient.id, link, token: token as string, phone, email }
}

/**
 * Long enough for a text sent on the request thread to reach the capture
 * gateway. Asserting "nothing arrived" straight after the request would pass
 * even if a text were on its way.
 */
async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 1_500))
}

async function redeem(
  request: APIRequestContext,
  invitation: Invitation,
  otp: string,
): Promise<APIResponse> {
  return request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp },
    failOnStatusCode: false,
  })
}

/** A six-digit code that is certainly not `code`. */
function notThe(code: string): string {
  return code === "000000" ? "111111" : "000000"
}

test("an invitation texts nothing until the patient asks for a code @portal", async ({
  api,
  request,
}) => {
  const invitation = await givePortalInvitation(api)

  await settle()
  expect(await sms.countFor(invitation.phone), "nothing is texted at invite time").toBe(0)

  // Without a code there is nothing to redeem with.
  const early = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: "123456" },
    failOnStatusCode: false,
  })
  expect(early.status()).toBe(401)

  const otp = await requestStepUpCode(invitation)
  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp },
  })
  expect(redeemed.status(), "the requested code redeems").toBe(200)
})

test("a new code retires the one before it @portal", async ({ api, request }) => {
  const invitation = await givePortalInvitation(api)

  const first = await requestStepUpCode(invitation)
  let second = first
  // Six random digits can repeat; ask until they differ, so what is asserted
  // below is retirement rather than a coincidence.
  while (second === first) second = await requestStepUpCode(invitation)

  const stale = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: first },
    failOnStatusCode: false,
  })
  expect(stale.status(), "the earlier code no longer works").toBe(401)

  const fresh = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: second },
  })
  expect(fresh.status(), "the newest code redeems").toBe(200)
})

test("asking for a code answers with nothing @portal", async ({ api, request }) => {
  const invitation = await givePortalInvitation(api)
  const before = await sms.countFor(invitation.phone)

  const asked = await request.post(`${BACKEND_URL}${REQUEST_CODE_PATH}`, {
    data: { token: invitation.token },
  })

  expect(asked.status()).toBe(202)
  const body = await asked.text()
  expect(body).toBe("")
  // The code went to the phone, and neither it nor the number came back.
  const code = stepUpCode(await sms.waitFor(invitation.phone, 10_000, before))
  expect(body).not.toContain(code)
  expect(body).not.toContain(invitation.phone)
})

test("the attempt cap still holds after a new code is sent @portal", async ({
  api,
  request,
}) => {
  const invitation = await givePortalInvitation(api)

  // Three wrong guesses against the first code, a resend, then two more
  // against the second: five in all, the engine's cap.
  const first = await requestStepUpCode(invitation)
  for (let i = 0; i < 3; i++) {
    expect((await redeem(request, invitation, notThe(first))).status()).toBe(401)
  }
  const second = await requestStepUpCode(invitation)
  for (let i = 0; i < 2; i++) {
    expect((await redeem(request, invitation, notThe(second))).status()).toBe(401)
  }

  // A resend did not buy more guesses: the right code is now refused, and
  // so is a request for another one.
  expect((await redeem(request, invitation, second)).status(), "capped").toBe(401)
  const again = await request.post(`${BACKEND_URL}${REQUEST_CODE_PATH}`, {
    data: { token: invitation.token },
    failOnStatusCode: false,
  })
  expect(again.status(), "a capped invitation cannot ask for a code").toBe(401)
})

test("recovery emails a link, texts nothing, and the link signs in @portal", async ({
  api,
  page,
  request,
}) => {
  // A patient who has been invited, so the practice has granted access.
  const invitation = await givePortalInvitation(api)
  const slug = new URL(invitation.link).pathname.split("/").pop() as string
  const letters = async () =>
    (await mail.received()).filter((m) => m.to.includes(invitation.email)).length
  const lettersBefore = await letters()
  const textsBefore = await sms.countFor(invitation.phone)

  const asked = await request.post(`${BACKEND_URL}/api/portal/practices/${slug}/recover`, {
    data: { email: invitation.email },
  })
  expect(asked.status(), "recovery always accepts").toBe(202)

  // A new email, and no text.
  await expect.poll(letters, { timeout: 10_000 }).toBeGreaterThan(lettersBefore)
  await settle()
  expect(await sms.countFor(invitation.phone), "nothing is texted at recover time").toBe(
    textsBefore,
  )

  const recovered = firstLink(await mail.waitFor(invitation.email))
  expect(recovered).not.toBe(invitation.link)
  await signInFromLink(page, recovered, invitation.phone)
})

test("a link that can no longer be used offers a new one @portal", async ({
  api,
  page,
  request,
}) => {
  const invitation = await givePortalInvitation(api)
  const slug = new URL(invitation.link).pathname.split("/").pop() as string

  // Spend it at the API first, so the browser arrives with a used link.
  const otp = await requestStepUpCode(invitation)
  expect((await redeem(request, invitation, otp)).status()).toBe(200)
  const textsAfterRedeem = await sms.countFor(invitation.phone)

  await page.goto(invitation.link)
  await page.getByTestId("portal-shell-request-code").click()

  // Not a dead end: the same sign-in the landing shows, saying why.
  await expect(page.getByTestId("portal-shell-link-ended")).toHaveText("That sign-in link has expired.")
  await expect(page.getByRole("button", { name: "Email me a sign-in link" })).toBeVisible()
  expect(page.url()).toContain(slug)
  await settle()
  expect(await sms.countFor(invitation.phone), "a spent link texts nobody").toBe(
    textsAfterRedeem,
  )
})

test("a wrong code offers the way to a new link @portal", async ({ api, page }) => {
  const invitation = await givePortalInvitation(api)
  const slug = new URL(invitation.link).pathname.split("/").pop() as string

  await page.goto(invitation.link)
  const otp = await askForCodeInPage(page, invitation.phone)
  await page.getByTestId("portal-shell-otp-input").fill(notThe(otp))
  await page.getByTestId("portal-shell-otp-submit").click()

  await expect(page.getByTestId("portal-shell-otp-error")).toBeVisible()
  await expect(page.getByTestId("portal-shell-otp-recover")).toHaveAttribute(
    "href",
    new RegExp(`/${slug}/recover$`),
  )
})

test("a single-use invitation cannot be redeemed twice @portal", async ({ api, request }) => {
  const invitation = await givePortalInvitation(api)
  const otp = await requestStepUpCode(invitation)

  const first = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp },
  })
  expect(first.status(), "first redemption mints a session").toBe(200)
  expect((await first.json()).session_token).toBeTruthy()

  const replay = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp },
    failOnStatusCode: false,
  })
  expect(replay.status(), "the same invitation cannot be spent twice").toBe(401)

  // And a spent invitation texts nobody anything.
  const again = await request.post(`${BACKEND_URL}${REQUEST_CODE_PATH}`, {
    data: { token: invitation.token },
    failOnStatusCode: false,
  })
  expect(again.status(), "a spent link cannot ask for a code").toBe(401)
})

test("a leaked link without the step-up code mints nothing @portal", async ({ api, request }) => {
  const invitation = await givePortalInvitation(api)
  const otp = await requestStepUpCode(invitation)

  // The §164.312(d) point: possession of the link is one factor.
  const wrongCode = otp === "000000" ? "111111" : "000000"
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
    data: { token: invitation.token, otp },
  })
  expect((await legitimate.json()).session_token, "the real code still redeems").toBeTruthy()
})

test("a patient session opens patient routes and no clinician one @portal", async ({
  api,
  request,
}) => {
  const invitation = await givePortalInvitation(api)
  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: await requestStepUpCode(invitation) },
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

/**
 * The default invitation says who it is from: the client's own clinician,
 * and the practice. Run on the engine's default wording (a sibling spec saves
 * a practice's own), with the clinician given a known name for the duration
 * and their own restored afterwards.
 */
async function withDefaultWordingAndName(
  api: ApiClient,
  name: string,
  run: (practiceName: string) => Promise<void>,
): Promise<void> {
  await api.delete("/api/portal/invite-template")
  const before = await api.get<{ name: string | null }>("/api/users/me")
  await api.patch("/api/users/me", { name })
  try {
    const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")
    const practice = await (
      await fetch(`${BACKEND_URL}/api/portal/practices/${encodeURIComponent(slug)}`)
    ).json()
    await run(practice.display_name as string)
  } finally {
    if (before.name) await api.patch("/api/users/me", { name: before.name })
  }
}

test("the default invitation names the client's clinician @portal", async ({ api }) => {
  const clinician = "Dr. Jane Smith"
  await withDefaultWordingAndName(api, clinician, async (practiceName) => {
    const { email, phone } = contactDetails()
    const patient = await givePatient(api, { email, phone })

    await api.post(`/api/patients/${patient.id}/portal-invite`)

    const message = await mail.waitFor(email)
    expect(message.subject).toBe(`${clinician} invited you to your patient portal`)
    const text = message.text.replace(/\r\n/g, "\n")
    expect(text).toContain(
      `${clinician} has invited you to the patient portal for ${practiceName}.`,
    )
    expect(text).not.toContain("{{")
  })
})

/**
 * A client who asked for a new link was not invited by anyone just now, so
 * the recovery email says what did happen and names the practice. The
 * clinician is given a known name so the test can show it is left out.
 */
test("a recovery email names the practice and invites nobody @portal", async ({ api }) => {
  const clinician = "Dr. Jane Smith"
  await withDefaultWordingAndName(api, clinician, async (practiceName) => {
    const invitation = await givePortalInvitation(api)
    const slug = new URL(invitation.link).pathname.split("/").pop() as string
    const received = async () =>
      (await mail.received()).filter((m) => m.to.includes(invitation.email)).length
    const before = await received()

    const asked = await fetch(`${BACKEND_URL}/api/portal/practices/${slug}/recover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: invitation.email }),
    })
    expect(asked.status).toBe(202)
    await expect.poll(received, { timeout: 10_000 }).toBeGreaterThan(before)

    const message = await mail.waitFor(invitation.email)
    expect(message.subject).toBe(`Your sign-in link for ${practiceName}`)
    const text = message.text.replace(/\r\n/g, "\n")
    expect(text).toContain(
      `Here's a new link to sign in to the patient portal for ${practiceName}.`,
    )
    expect(text).not.toContain("invited")
    expect(text).not.toContain(clinician)
    expect(text).not.toContain("{{")
  })
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
 * carried, ask for a code, type the code the text carried, and arrive in the
 * portal.
 *
 * The other tests in this file post to the redeem route directly, which
 * proves the credential rules but not that the link goes anywhere. This one
 * opens the captured link in a browser and never types a URL of its own.
 */
test("a patient signs in from the link in their email @portal", async ({ api, page }) => {
  const invitation = await givePortalInvitation(api)

  await page.goto(invitation.link)

  // The page offers to text a code; it does not arrive by itself.
  await expect(page.getByTestId("portal-shell-request-code")).toHaveText("Text me a code")
  const otp = await askForCodeInPage(page, invitation.phone)
  await page.getByTestId("portal-shell-otp-input").fill(otp)
  await page.getByTestId("portal-shell-otp-submit").click()

  await expect(page.getByTestId("portal-shell-active")).toBeVisible()
  await expect(page.getByTestId("portal-shell-practice-name")).toBeVisible()

  // The address bar still names the practice and no longer holds the
  // invitation: a reloaded tab or a shared screen is not a credential. Which
  // address depends on the deployment: `/portal/{slug}` where the portal
  // shares the app's host, `/{slug}` where it has a host of its own (this
  // stack, whose link is redirected there with its fragment intact).
  expect(page.url()).not.toContain("invite")
  const slug = new URL(invitation.link).pathname.split("/").pop()
  await expect(page).toHaveURL(new RegExp(`/(portal/)?${slug}$`))
})
