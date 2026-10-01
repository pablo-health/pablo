// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Client messages in the Inbox, over the real stack: a client writes through
 * the portal, and the practice reads each message as its own Inbox item,
 * inside the conversation around it, answers one, and the client sees the
 * answer.
 *
 * What only a browser against the stack can prove:
 *
 *   1. **Each client message is its own item**, under the Messages filter,
 *      and the one Inbox badge counts them.
 *   2. **A reply typed here reaches the client's portal thread**, and it
 *      resolves the message replied to and no other: the earlier one stays
 *      open until the clinician says so ("Also mark … handled?" → Yes).
 *   3. **Handled messages move to Done**, not away.
 *   4. **A patient without a grant is absent** — the second practice's
 *      clinician sees none of it, over real HTTP.
 *
 * Every worker shares one practice, so items are found by their own ids and
 * by text this test wrote, never by position or by total counts. Nothing here
 * changes the shared clinician's "earlier messages" preference, which stays
 * at its default of asking.
 */

import type { Locator, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const PATIENT_THREADS = `${BACKEND_URL}/api/patient/messages/threads`

interface ThreadDetail {
  id: string
  messages: { id: string; sender: string; body: string }[]
}

interface InboxList {
  data: { kind: string; source_id: string; detail: string | null }[]
}

let sequence = 0
function stamp(): string {
  return `${Date.now().toString(36)}${(sequence++).toString(36)}`
}

function inboxRow(page: Page, sourceId: string): Locator {
  return page.locator(`[data-testid="inbox-row"][data-source-id="${sourceId}"]`)
}

test("each client message is its own Inbox item, and a reply answers the one replied to @portal", async ({
  api,
  request,
  signedInPage: page,
}) => {
  const tag = stamp()
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { first_name: `Msg${tag}`, email, phone })
  const patientName = `Msg${tag} Patient`
  const session = await givePortalSession(api, request, patient.id, email, phone)
  const asClient = { headers: { Authorization: `Bearer ${session}` } }

  const first = `first message ${tag}`
  const second = `second message ${tag}`
  const started = await request.post(PATIENT_THREADS, {
    ...asClient,
    data: { subject: `Question ${tag}`, body: first },
  })
  expect(started.status()).toBe(201)
  const thread = (await started.json()) as ThreadDetail
  const firstId = thread.messages[0].id
  const followUp = await request.post(`${PATIENT_THREADS}/${thread.id}/messages`, {
    ...asClient,
    data: { body: second },
  })
  expect(followUp.status()).toBe(201)
  const secondId = ((await followUp.json()) as { id: string }).id

  await page.goto("/dashboard/inbox?filter=messages")

  // One nav item, one badge; Messages is not a nav item of its own.
  const nav = page.getByRole("navigation", { name: "Main navigation" })
  await expect(nav.getByRole("link", { name: /Inbox/ })).toBeVisible()
  await expect(nav.getByTestId("nav-badge-inbox")).toBeVisible()
  await expect(nav.getByRole("link", { name: /^Messages/ })).toHaveCount(0)

  // Two messages, two items, each with whose it is and what it said.
  await expect(inboxRow(page, firstId)).toContainText(patientName)
  await expect(inboxRow(page, firstId)).toContainText(first)
  await expect(inboxRow(page, secondId)).toContainText(second)

  // Opening the newer one shows it inside the conversation, marked.
  await inboxRow(page, secondId).click()
  const view = page.getByTestId("thread-view")
  await expect(view).toContainText(patientName)
  const clientMessages = view.getByTestId("thread-message-client")
  await expect(clientMessages).toHaveCount(2)
  await expect(clientMessages.nth(1)).toHaveAttribute("data-highlighted", "true")

  // A reply goes into the client's thread and resolves that message only.
  const answer = `reply ${tag}`
  await view.getByTestId("thread-reply-input").fill(answer)
  await view.getByTestId("thread-reply-send").click()
  await expect(view.getByTestId("thread-message-practice")).toContainText(answer)
  await expect(inboxRow(page, secondId)).toHaveCount(0)
  await expect(inboxRow(page, firstId)).toBeVisible()

  const seen = (await (
    await request.get(`${PATIENT_THREADS}/${thread.id}`, asClient)
  ).json()) as ThreadDetail
  expect(seen.messages.map((m) => m.body)).toEqual([first, second, answer])
  expect(seen.messages[2].sender).toBe("clinician")

  // Asked once about the earlier one; Yes marks it handled this time.
  const prompt = page.getByTestId("inbox-earlier-prompt")
  await expect(prompt).toContainText(`Also mark ${patientName}'s earlier message handled?`)
  await prompt.getByRole("button", { name: "Yes" }).click()
  await expect(page.getByTestId("inbox-earlier-handled")).toContainText(
    "1 earlier message marked handled",
  )
  await expect(inboxRow(page, firstId)).toHaveCount(0)

  // Both are in Done, not gone.
  await page.goto("/dashboard/inbox?filter=messages&view=done")
  await expect(inboxRow(page, secondId)).toContainText("Replied")
  await expect(inboxRow(page, firstId)).toContainText("Marked handled")
})

test("another practice's clinician sees none of it @portal", async ({
  api,
  otherPracticeApi,
  request,
}) => {
  const tag = stamp()
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const session = await givePortalSession(api, request, patient.id, email, phone)
  const sent = await request.post(PATIENT_THREADS, {
    headers: { Authorization: `Bearer ${session}` },
    data: { subject: `Private ${tag}`, body: `only for my practice ${tag}` },
  })
  const messageId = ((await sent.json()) as ThreadDetail).messages[0].id

  const ours = await api.get<InboxList>("/api/inbox?view=open&kinds=portal_message")
  expect(ours.data.map((item) => item.source_id), "control: the practice sees it").toContain(
    messageId,
  )

  const theirs = await otherPracticeApi.get<InboxList>("/api/inbox?view=open&kinds=portal_message")
  expect(theirs.data.map((item) => item.source_id)).not.toContain(messageId)
  expect(theirs.data.map((item) => item.detail)).not.toContain(`only for my practice ${tag}`)
})
