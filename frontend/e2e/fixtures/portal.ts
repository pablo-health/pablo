// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Invite a patient and read back both step-up factors, factored out of
 * `portal-auth.spec.ts` / `portal-consent.spec.ts` so messaging specs use
 * the same capture seam rather than a second one.
 *
 * The link arrives with its token in the URL fragment, which is never sent
 * to a server; the step-up code arrives on a separate channel. Both are
 * read from the stand-in their channel is wired to on this stack — the
 * mail server and the fake text-message gateway — never invented here.
 */

import type { APIRequestContext, Page } from "@playwright/test"
import { expect } from "@playwright/test"
import type { ApiClient } from "./api"
import { firstLink, mail } from "./mail"
import { sms, stepUpCode } from "./sms"
import { BACKEND_URL } from "./stack"

const REDEEM_PATH = "/api/patient/auth/redeem"

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
  otp: string
}

/** Invite the patient and read both factors back, without spending either. */
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

  return { link, token: token as string, otp: stepUpCode(await sms.waitFor(phone)) }
}

/** Redeem an invitation at the API and return the patient's session token. */
export async function redeemPortalInvitation(
  request: APIRequestContext,
  invitation: PortalInvitation,
): Promise<string> {
  const redeemed = await request.post(`${BACKEND_URL}${REDEEM_PATH}`, {
    data: { token: invitation.token, otp: invitation.otp },
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

/** Sign in through the shell exactly as the patient does: open the link, type the code. */
export async function signInToPortal(page: Page, invitation: PortalInvitation): Promise<void> {
  await page.goto(invitation.link)
  await page.getByTestId("portal-shell-otp-input").fill(invitation.otp)
  await page.getByTestId("portal-shell-otp-submit").click()
  await expect(page.getByTestId("portal-shell-active")).toBeVisible()
}
