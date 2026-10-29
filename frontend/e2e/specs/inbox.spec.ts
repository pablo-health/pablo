// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Inbox over the real stack: a refill request and a client message both
 * arrive in the one list, and handling each where it is handled takes it off.
 *
 *   1. **A refill request shows up**, and answering it on the Refills page —
 *      reached from the item's own link — clears it. The Inbox holds no copy
 *      of its own, so there is nothing left to go stale.
 *   2. **A client message shows up**, and replying from the Inbox clears it
 *      and files it under Done.
 *
 * Every worker shares one practice, so items are found by their own ids,
 * never by position or by counts.
 */

import type { Locator, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

function inboxRow(page: Page, sourceId: string): Locator {
  return page.locator(`[data-testid="inbox-row"][data-source-id="${sourceId}"]`)
}

let sequence = 0
function stamp(): string {
  return `${Date.now().toString(36)}${(sequence++).toString(36)}`
}

test("a refill request is in the Inbox until it is answered on the Refills page @portal", async ({
  api,
  request,
  signedInPage: page,
}) => {
  const tag = stamp()
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const medication = await api.post<{ id: string; drug_name: string }>(
    `/api/patients/${patient.id}/medications`,
    { drug_name: `E2E Inbox Sertraline ${tag}`, dose: "50 mg", status: "active" },
  )
  const headers = {
    Authorization: `Bearer ${await givePortalSession(api, request, patient.id, email, phone)}`,
  }
  const asked = await request.post(`${BACKEND_URL}/api/patient/refills`, {
    headers,
    data: { medication_id: medication.id, patient_note: `Out on Friday ${tag}` },
  })
  expect(asked.status()).toBe(201)
  const refillId = ((await asked.json()) as { id: string }).id

  // It is in the whole Inbox, not only behind a filter.
  await page.goto("/dashboard/inbox")
  const row = inboxRow(page, refillId)
  await expect(row).toContainText(medication.drug_name)

  await row.click()
  await expect(page.getByTestId("inbox-item-refill")).toContainText(`Out on Friday ${tag}`)
  await page.getByRole("link", { name: "Answer on the Refills page" }).click()
  await expect(page).toHaveURL(/\/dashboard\/refills$/)

  const refillRow = page.getByTestId(`refill-row-${refillId}`)
  await refillRow.getByTestId("refill-decide-approved").click()
  await refillRow.getByTestId("refill-confirm-submit").click()
  await expect(refillRow, "an answered request leaves the queue").toHaveCount(0)

  await page.goto("/dashboard/inbox?filter=refills")
  await expect(page.getByTestId("inbox-list").or(page.getByTestId("inbox-empty"))).toBeVisible()
  await expect(inboxRow(page, refillId), "answered at its source, it leaves the Inbox").toHaveCount(0)
})

test("a client message is in the Inbox until it is answered, then it is under Done @portal", async ({
  api,
  request,
  signedInPage: page,
}) => {
  const tag = stamp()
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { first_name: `Inbox${tag}`, email, phone })
  const session = await givePortalSession(api, request, patient.id, email, phone)
  const sent = await request.post(`${BACKEND_URL}/api/patient/messages/threads`, {
    headers: { Authorization: `Bearer ${session}` },
    data: { subject: `Question ${tag}`, body: `can we move Thursday ${tag}` },
  })
  expect(sent.status()).toBe(201)
  const messageId = ((await sent.json()) as { messages: { id: string }[] }).messages[0].id

  await page.goto("/dashboard/inbox")
  const row = inboxRow(page, messageId)
  await expect(row).toContainText(`Inbox${tag} Patient`)
  await expect(row).toContainText(`can we move Thursday ${tag}`)

  await row.click()
  const view = page.getByTestId("thread-view")
  await view.getByTestId("thread-reply-input").fill(`Yes, 3pm works ${tag}`)
  await view.getByTestId("thread-reply-send").click()
  await expect(view.getByTestId("thread-message-practice")).toContainText(`Yes, 3pm works ${tag}`)
  await expect(inboxRow(page, messageId), "answered, it leaves the open list").toHaveCount(0)

  await page.getByRole("button", { name: "Done" }).click()
  await expect(inboxRow(page, messageId)).toContainText("Replied")
})

/**
 * The practice's reply-time target dates each waiting message in the Inbox.
 * It is set under Messaging in portal settings, and it is read when the Inbox
 * is: a longer target moves a message that is already waiting later.
 */
test("a client message carries the practice's reply-by day in the Inbox @portal", async ({
  api,
  request,
  signedInPage: page,
}) => {
  type Item = { source_id: string; due_at: string | null }
  type Settings = { message_reply_target_days: number }
  const tag = stamp()
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { first_name: `Reply${tag}`, email, phone })
  const session = await givePortalSession(api, request, patient.id, email, phone)
  const before = await api.get<Settings>("/api/portal/settings")

  try {
    await page.goto("/dashboard/settings/portal")
    const target = page.getByRole("combobox", { name: "Message reply target" })
    await target.selectOption("1")
    await expect
      .poll(async () => (await api.get<Settings>("/api/portal/settings")).message_reply_target_days)
      .toBe(1)

    const sent = await request.post(`${BACKEND_URL}/api/patient/messages/threads`, {
      headers: { Authorization: `Bearer ${session}` },
      data: { subject: `Reply-by ${tag}`, body: `when can I come in ${tag}` },
    })
    expect(sent.status()).toBe(201)
    const messageId = ((await sent.json()) as { messages: { id: string }[] }).messages[0].id
    const dueOf = async (): Promise<number> => {
      const listed = await api.get<{ data: Item[] }>("/api/inbox?kinds=portal_message")
      const item = listed.data.find((i) => i.source_id === messageId)
      expect(item?.due_at, "a waiting message has a reply-by time").toBeTruthy()
      return new Date(item?.due_at ?? 0).getTime()
    }

    await page.goto("/dashboard/inbox")
    await expect(inboxRow(page, messageId).getByTestId("refill-due")).toHaveText(/^Due /)
    const soon = await dueOf()

    await api.put("/api/portal/settings", { message_reply_target_days: 5 })
    expect(await dueOf(), "a longer target moves a waiting message later").toBeGreaterThan(soon)
  } finally {
    await api.put("/api/portal/settings", {
      message_reply_target_days: before.message_reply_target_days,
    })
  }
})
