// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Invite a patient and read back both step-up factors, factored out of
 * `portal-auth.spec.ts` / `portal-consent.spec.ts` so messaging specs use
 * the same capture seam rather than a second one.
 *
 * The link arrives with its token in the URL fragment, which is never sent
 * to a server; the step-up code arrives on a separate channel, texted when
 * the patient asks for it from the page the link opens. Both are read from
 * the stand-in their channel is wired to on this stack — the mail server and
 * the fake text-message gateway — never invented here.
 */

import type { APIRequestContext, Page } from "@playwright/test"
import { expect } from "@playwright/test"
import type { ApiClient } from "./api"
import { firstLink, mail } from "./mail"
import { sms, stepUpCode } from "./sms"
import { BACKEND_URL } from "./stack"

const REDEEM_PATH = "/api/patient/auth/redeem"
const REQUEST_CODE_PATH = "/api/patient/auth/request-code"

let sequence = 0

/** A fresh address and number per invitation, so one test never reads another's. */
export function givePortalContactDetails(): { email: string; phone: string } {
  const stamp = `${Date.now().toString(36)}${(sequence++).toString(36)}`
  return { email: `portal-msg-${stamp}@example.com`, phone: `+1502555${String(sequence).padStart(4, "0")}` }
}

export interface PortalInvitation {
  /** The whole link, exactly as the email carried it. */
  link: string
  token: string
  /** Where the code goes once it is asked for. */
  phone: string
}

/** Invite the patient and read the link back. No code exists until one is asked for. */
export async function givePortalInvitation(
  api: ApiClient,
  patientId: string,
  email: string,
  phone: string,
): Promise<PortalInvitation> {
  await api.post(`/api/patients/${patientId}/portal-invite`)

  const link = firstLink(await mail.waitFor(email))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  expect(token, `the invitation email carries a token: ${link}`).toBeTruthy()

  return { link, token: token as string, phone }
}

/**
 * Ask for a code for this link at the API — what "Text me a code" does — and
 * read the text that arrives. Waits for a message beyond the ones the number
 * already had, so a resend is never answered with the code it replaced.
 */
export async function requestStepUpCode(
  invitation: Pick<PortalInvitation, "token" | "phone">,
): Promise<string> {
  const before = await sms.countFor(invitation.phone)
  const asked = await fetch(`${BACKEND_URL}${REQUEST_CODE_PATH}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: invitation.token }),
  })
  expect(asked.status, "the code is sent").toBe(202)
  return stepUpCode(await sms.waitFor(invitation.phone, 10_000, before))
}

/** Tap "Text me a code" on the page the link opened, and read the text. */
export async function askForCodeInPage(page: Page, phone: string): Promise<string> {
  const before = await sms.countFor(phone)
  await page.getByTestId("portal-shell-request-code").click()
  await expect(page.getByTestId("portal-shell-otp-input")).toBeVisible()
  return stepUpCode(await sms.waitFor(phone, 10_000, before))
}

/** Ask for a code, redeem at the API and return the patient's session token. */
export async function redeemPortalInvitation(
  request: APIRequestContext,
  invitation: PortalInvitation,
): Promise<string> {
  const otp = await requestStepUpCode(invitation)
  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp },
  })
  expect(redeemed.status(), "the invitation redeems").toBe(200)
  return (await redeemed.json()).session_token as string
}

/** Invite, invite a patient and redeem in one call, for API-only specs. */
export async function givePortalSession(
  api: ApiClient,
  request: APIRequestContext,
  patientId: string,
  email: string,
  phone: string,
): Promise<string> {
  const invitation = await givePortalInvitation(api, patientId, email, phone)
  return redeemPortalInvitation(request, invitation)
}

/**
 * Sign in through the shell exactly as the patient does: open the link, ask
 * for a code, type it. The patient lands on Home; a spec about one section
 * goes on with {@link openPortalSection}.
 */
export async function signInToPortal(page: Page, invitation: PortalInvitation): Promise<void> {
  await signInFromLink(page, invitation.link, invitation.phone)
}

/**
 * The one place a spec walks the shell's sign-in: open `link`, tap "Text me
 * a code", read the text sent to `phone`, type it, and arrive signed in.
 * Specs that already hold a link (an invite they read themselves, a recovery
 * email) call this rather than repeating the steps.
 */
export async function signInFromLink(page: Page, link: string, phone: string): Promise<void> {
  await page.goto(link)
  const otp = await askForCodeInPage(page, phone)
  await page.getByTestId("portal-shell-otp-input").fill(otp)
  await page.getByTestId("portal-shell-otp-submit").click()
  await expect(page.getByTestId("portal-shell-active")).toBeVisible()
}

/**
 * Open one section from Home the way a patient does: tap its tile. The
 * section is a page of its own (`/portal/{slug}/{section}`, or `/{slug}/{section}`
 * on a portal host), so a reload after this stays on it.
 */
export async function openPortalSection(page: Page, section: string): Promise<void> {
  await page.getByTestId(`portal-home-tile-${section}`).click()
  await expect(page).toHaveURL(new RegExp(`/${section}$`))
  await expect(page.getByTestId(`portal-section-${section}`)).toBeVisible()
}
