// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's Messages page, over the real stack: a client writes through
 * the portal, and the practice reads it grouped as a conversation or one
 * message at a time, answers it, and the client sees the answer.
 *
 * What only a browser against the stack can prove:
 *
 *   1. **Both views show what the client sent** — the conversation, with
 *      whose it is and its unread count, and each message on its own row.
 *   2. **The badge counts it**, and opening the conversation clears it.
 *   3. **A reply typed here reaches the client's portal thread.**
 *   4. **A patient without a grant is absent** — the second practice's
 *      clinician sees none of it, over real HTTP.
 *
 * Every worker shares one practice, so rows are found by text this test
 * wrote, never by position or by total counts.
 */

import { expect, test } from "../fixtures/auth"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const PATIENT_THREADS = `${BACKEND_URL}/api/patient/messages/threads`

interface ThreadDetail {
  id: string
  messages: { sender: string; body: string }[]
}

let sequence = 0
function stamp(): string {
  return `${Date.now().toString(36)}${(sequence++).toString(36)}`
}

test("a client's messages show grouped and one by one, and a reply reaches the portal @portal", async ({
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
  const threadId = ((await started.json()) as ThreadDetail).id
  const followUp = await request.post(`${PATIENT_THREADS}/${threadId}/messages`, {
    ...asClient,
    data: { body: second },
  })
  expect(followUp.status()).toBe(201)

  await page.goto("/dashboard/messages")

  // The badge counts at least this conversation.
  const nav = page.getByRole("navigation", { name: "Main navigation" })
  await expect(nav.getByRole("link", { name: /Messages/ })).toBeVisible()
  await expect(nav.getByTestId("nav-badge-unread-messages")).toBeVisible()

  // Grouped: one row for the conversation, with whose it is and two unread.
  const conversation = page.getByTestId("conversation-row").filter({ hasText: `Question ${tag}` })
  await expect(conversation).toHaveCount(1)
  await expect(conversation).toContainText(patientName)
  await expect(conversation.getByLabel("2 unread")).toBeVisible()
  // No message text in the grouped view.
  await expect(page.getByTestId("conversation-list")).not.toContainText(first)

  // Ungrouped: each message on its own row, newest first.
  await page.getByRole("tab", { name: "Messages" }).click()
  const mine = page.getByTestId("message-row").filter({ hasText: tag })
  await expect(mine).toHaveCount(2)
  await expect(mine.nth(0)).toContainText(second)
  await expect(mine.nth(1)).toContainText(first)
  await expect(mine.nth(0)).toHaveAttribute("data-unread", "true")

  // Opening one lands in the conversation and marks it read.
  await mine.nth(1).click()
  const thread = page.getByTestId("thread-view")
  await expect(thread).toContainText(patientName)
  await expect(thread.getByTestId("thread-message-client")).toHaveCount(2)
  await expect(mine.nth(0)).toHaveAttribute("data-unread", "false")

  // A reply typed here goes into the client's thread.
  const answer = `reply ${tag}`
  await thread.getByTestId("thread-reply-input").fill(answer)
  await thread.getByTestId("thread-reply-send").click()
  await expect(thread.getByTestId("thread-message-practice")).toContainText(answer)

  const seen = (await (
    await request.get(`${PATIENT_THREADS}/${threadId}`, asClient)
  ).json()) as ThreadDetail
  expect(seen.messages.map((m) => m.body)).toEqual([first, second, answer])
  expect(seen.messages[2].sender).toBe("clinician")

  // Back in the grouped view, the conversation has nothing unread.
  await page.getByRole("tab", { name: "Conversations" }).click()
  await expect(conversation).toHaveAttribute("data-unread", "false")
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
  await request.post(PATIENT_THREADS, {
    headers: { Authorization: `Bearer ${session}` },
    data: { subject: `Private ${tag}`, body: `only for my practice ${tag}` },
  })

  const theirThreads = await otherPracticeApi.get<{ data: { subject: string | null }[] }>(
    "/api/message-threads?status=all",
  )
  expect(theirThreads.data.map((t) => t.subject)).not.toContain(`Private ${tag}`)
  const theirMessages = await otherPracticeApi.get<{ data: { body: string }[] }>(
    "/api/message-threads/messages",
  )
  expect(theirMessages.data.map((m) => m.body)).not.toContain(`only for my practice ${tag}`)
})
