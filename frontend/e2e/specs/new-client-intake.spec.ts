// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Adding a client starts their intake: word the invitation, add the client,
 * choose what they should do, review it with the email in view, send it —
 * and the email that arrives is the one that was previewed.
 *
 * The last clause is the one worth a browser. The preview and the send are
 * rendered by one function on the server, and this reads the real message
 * off the stack's mail server and holds it to the preview, character for
 * character, with only the sign-in link (which does not exist until Send)
 * standing in.
 *
 * Also walks the chart's Intake tab: reachable by URL, honest when nothing
 * has been sent, and the place the same flow starts from for an existing
 * client.
 */

import type { Page } from "@playwright/test"
import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { firstLink, mail } from "../fixtures/mail"
import { givePortalContactDetails, requestStepUpCode } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { sms } from "../fixtures/sms"

const PREVIEW_LINK = "[personal sign-in link]"
const SEEDED_FORM = "Intake"

const WORDING = {
  subject: "{{practice_name}}: a few things before we meet",
  body: [
    "Hi {{client_first_name}},",
    "",
    "Before our first session, please fill in:",
    "{{forms}}",
    "",
    "Sign in here: {{portal_link}}",
    "We will text you a code. The link works for {{link_expiry}}.",
  ].join("\n"),
}

async function useStandardWording(api: ApiClient): Promise<void> {
  await api.delete("/api/portal/invite-template")
}

async function wordTheInvitationInSettings(page: Page): Promise<void> {
  await page.goto("/dashboard/settings/portal")
  const card = page.getByTestId("invite-email-card")
  await expect(card).toBeVisible()

  await card.getByLabel("Subject").fill(WORDING.subject)
  await card.getByLabel("Message").fill(WORDING.body)

  // The preview is the server's rendering for an example client.
  const preview = page.getByTestId("invite-email-card-preview")
  await expect(preview).toContainText("Hi Alex,")
  await expect(preview).toContainText(PREVIEW_LINK)

  await card.getByRole("button", { name: "Save", exact: true }).click()
  await expect(card.getByText("Saved", { exact: true })).toBeVisible()
}

test.describe("A new client's intake", () => {
  test.afterEach(async ({ api }) => {
    // The wording is the practice's, and later specs in this worker read
    // invitation emails for their own links.
    await useStandardWording(api)
  })

  test("the practice adds a client, reviews the email, and the client gets exactly that email @portal", async ({
    api,
    signedInPage: page,
  }) => {
    await wordTheInvitationInSettings(page)

    // --- add the client -------------------------------------------------
    const { email, phone } = givePortalContactDetails()
    const lastName = `Intake${Date.now().toString(36)}`
    await page.goto("/dashboard/patients")
    await page.getByRole("button", { name: /add patient/i }).click()
    await page.getByLabel(/first name/i).fill("Robin")
    await page.getByLabel(/last name/i).fill(lastName)
    await page.getByLabel(/email/i).fill(email)
    await page.getByLabel(/phone/i).fill(phone)
    await page.getByRole("button", { name: /create patient/i }).click()

    // --- the dialog runs on to what they should do ----------------------
    const next = page.getByTestId("new-client-next-step")
    await expect(next).toBeVisible()
    await expect(next).toContainText("What should Robin do next?")
    await next.getByRole("checkbox", { name: SEEDED_FORM, exact: true }).check()
    await expect(next.getByRole("checkbox", { name: "Invite them to the portal" })).toBeChecked()
    await next.getByRole("button", { name: "Review" }).click()

    // --- review, with the email in view --------------------------------
    await expect(page.getByTestId("send-forms-review-list")).toHaveText(SEEDED_FORM)
    await expect(page.getByTestId("send-forms-review-portal")).toContainText(email)
    await page.getByRole("button", { name: "Preview email" }).click()
    await expect(page.getByTestId("invite-email-preview-to")).toHaveText(email)
    const previewSubject = (await page.getByTestId("invite-email-preview-subject").textContent())!
    const previewText = (await page.getByTestId("invite-email-preview-text").textContent())!
    expect(previewSubject).toMatch(/: a few things before we meet$/)
    expect(previewText).toContain("Hi Robin,")
    expect(previewText).toContain(`- ${SEEDED_FORM}`)
    expect(previewText).toContain(PREVIEW_LINK)
    expect(previewText).toContain("The link works for 15 minutes.")

    // Nothing has gone anywhere yet.
    expect((await mail.received()).filter((m) => m.to.includes(email))).toEqual([])

    // --- send ----------------------------------------------------------
    await page.getByTestId("send-forms-send").click()
    await expect(page.getByTestId("send-forms-outcome")).toHaveText(
      "Sent. They will get a link by email and a code by text.",
    )

    // --- the email that arrived is the one that was previewed ----------
    const arrived = await mail.waitFor(email)
    const link = firstLink(arrived)
    expect(arrived.subject).toBe(previewSubject)
    // SMTP carries lines as CRLF; that is the transport, not the wording.
    const received = arrived.text.replace(/\r\n/g, "\n").trim()
    expect(received).toBe(previewText.replace(PREVIEW_LINK, link).trim())
    // And it is a working invitation: nothing was texted yet, and the link
    // gets a code the moment one is asked for.
    expect(await sms.countFor(phone)).toBe(0)
    const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
    expect(await requestStepUpCode({ token: token as string, phone })).toMatch(/^\d{6}$/)

    await page.getByRole("button", { name: "Done" }).click()
    await expect(page.getByRole("dialog")).not.toBeVisible()

    // --- the chart's Intake tab shows what was sent ---------------------
    const patients = await api.get<{ data: { id: string; last_name: string }[] }>(
      `/api/patients?search=${lastName}`,
    )
    const [client] = patients.data.filter((p) => p.last_name === lastName)
    await page.goto(`/dashboard/patients/${client.id}?tab=intake`)
    await expect(page.getByTestId("intake-assignments")).toContainText(SEEDED_FORM)
  })

  test("the chart's Intake tab says nothing has been sent, and starts the same flow", async ({
    api,
    signedInPage: page,
  }) => {
    const client = await givePatient(api)

    await page.goto(`/dashboard/patients/${client.id}`)
    await page.getByRole("tab", { name: "Intake" }).click()
    await expect(page.getByTestId("intake-empty")).toHaveText(
      "No forms have been sent to this client yet.",
    )

    await page.getByTestId("send-intake-form-button").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByTestId("send-forms-choose")).toBeVisible()
    await expect(dialog.getByRole("checkbox", { name: SEEDED_FORM, exact: true })).toBeVisible()
    await dialog.getByRole("button", { name: "Cancel" }).click()
    await expect(dialog).not.toBeVisible()

    const assignments = await api.get<unknown[]>(`/api/patients/${client.id}/intake-assignments`)
    expect(assignments, "leaving the flow sends nothing").toEqual([])
  })

  test("a new client can be added without sending anything", async ({ api, signedInPage: page }) => {
    const lastName = `Later${Date.now().toString(36)}`
    await page.goto("/dashboard/patients")
    await page.getByRole("button", { name: /add patient/i }).click()
    await page.getByLabel(/first name/i).fill("Sam")
    await page.getByLabel(/last name/i).fill(lastName)
    await page.getByRole("button", { name: /create patient/i }).click()

    await page.getByTestId("new-client-next-step").getByRole("button", { name: "Not now" }).click()
    await expect(page.getByRole("dialog")).not.toBeVisible()

    const patients = await api.get<{ data: { id: string; last_name: string }[] }>(
      `/api/patients?search=${lastName}`,
    )
    const [client] = patients.data.filter((p) => p.last_name === lastName)
    expect(client, "the client was still added").toBeTruthy()
    expect(await api.get<unknown[]>(`/api/patients/${client.id}/intake-assignments`)).toEqual([])
  })
})
