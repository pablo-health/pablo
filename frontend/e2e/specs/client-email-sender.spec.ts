// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice owner sets who client email is from, and a real invitation
 * carries it.
 *
 * What only the real stack can prove: the card saves through the real route
 * into the platform table, and the invitation the portal sends afterwards
 * leaves through the real SMTP sender with the practice's name on the From
 * line and its Reply-To. The practice on this stack has no domain that can
 * send, so the From address stays the deployment's own — which is the
 * fallback a practice sees while its domain is being set up.
 *
 * Deliberately not here: the verified-domain From address, the choice among
 * several domains and the personal-mailbox refusal, which need a domain whose
 * email has been verified and are proven against Postgres in
 * backend/tests_integration/database/test_client_sender_db.py; validation,
 * permissions and the audit row, in backend/tests/test_client_sender.py.
 */

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { mail } from "../fixtures/mail"
import { givePatient } from "../fixtures/scenarios"

const SENDER_URL = "/api/practice/email-sender"
/** The deployment's own address on this stack (SMTP_FROM in docker-compose.e2e.yml). */
const DEPLOYMENT_FROM = "bookings@example.test"

interface SenderFields {
  sender_name: string | null
  sender_local_part: string | null
  reply_to: string | null
}

async function chosen(api: ApiClient): Promise<SenderFields> {
  return (await api.get<{ chosen: SenderFields }>(SENDER_URL)).chosen
}

test.describe("Who client email is from", () => {
  let original: SenderFields

  test.beforeEach(async ({ api }) => {
    original = await chosen(api)
  })

  test.afterEach(async ({ api }) => {
    // The practice is shared by every spec in this worker.
    await api.put(SENDER_URL, original)
  })

  test("the owner sets the sender name and reply-to, and an invitation carries both", async ({
    api,
    signedInPage: page,
  }) => {
    const stamp = Date.now().toString(36)
    const senderName = `Jordan Rivera, LCSW ${stamp}`
    const replyTo = `frontdesk-${stamp}@example.com`

    await page.goto("/dashboard/settings/portal")
    const card = page.getByTestId("client-email-sender-card")
    await card.getByLabel("Sender name").fill(senderName)
    await card.getByLabel("Replies go to").fill(replyTo)

    await expect(page.getByTestId("client-email-preview")).toContainText(
      `Clients see: ${senderName} <${DEPLOYMENT_FROM}> · replies go to ${replyTo}`,
    )
    await card.getByRole("button", { name: "Save", exact: true }).click()
    await expect(card.getByText("Saved")).toBeVisible()

    await page.reload()
    await expect(page.getByTestId("client-email-sender-card").getByLabel("Sender name")).toHaveValue(
      senderName,
    )

    const email = `sender-${stamp}@example.com`
    const patient = await givePatient(api, { email, phone: "+15005550199" })
    await api.post(`/api/patients/${patient.id}/portal-invite`)

    const invitation = await mail.waitFor(email)
    // The comma in the name is quoted, so a mail client reads one sender.
    expect(invitation.from_header).toBe(`"${senderName}" <${DEPLOYMENT_FROM}>`)
    expect(invitation.reply_to).toBe(replyTo)
  })
})
