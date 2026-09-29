// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Messages page when the practice has turned Messages off.
 *
 * Over the real stack, in a practice of its own (fixtures/freshPractice.ts):
 * a client writes, the practice turns Messages off, and the page still shows
 * what the client sent — it is the practice's to read — while offering no
 * reply, which would land somewhere the client can no longer open. The client
 * side of the same switch (their routes answering 404) is in
 * portal-offering.spec.ts.
 */

import { expect, test } from "../fixtures/auth"
import { signInToFreshPractice } from "../fixtures/freshPractice"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const SETTINGS = "/api/portal/settings"

test("with Messages off, what clients sent stays readable and there is no reply box @portal", async ({
  browser,
  request,
}) => {
  const { page, context, api } = await signInToFreshPractice(browser, "messages")
  try {
    await api.put(SETTINGS, { enabled: true, modules: { messaging: true } })
    const tag = Date.now().toString(36)
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone })
    const session = await givePortalSession(api, request, patient.id, email, phone)
    const sent = await request.post(`${BACKEND_URL}/api/patient/messages/threads`, {
      headers: { Authorization: `Bearer ${session}` },
      data: { subject: `Before it went off ${tag}`, body: `sent while on ${tag}` },
    })
    expect(sent.status()).toBe(201)

    await api.put(SETTINGS, { modules: { messaging: false } })

    await page.goto("/dashboard/messages")
    await expect(page.getByTestId("messages-portal-off")).toContainText(
      "Messages are turned off in your client portal.",
    )
    const row = page.getByTestId("conversation-row").filter({ hasText: `Before it went off ${tag}` })
    await row.click()

    const thread = page.getByTestId("thread-view")
    await expect(thread.getByTestId("thread-message-client")).toContainText(`sent while on ${tag}`)
    await expect(thread.getByTestId("thread-reply-input")).toHaveCount(0)
    await expect(thread.getByTestId("thread-replies-off")).toContainText(
      "Turn Messages back on to reply.",
    )
  } finally {
    await api.put(SETTINGS, { modules: { messaging: true } }).catch(() => undefined)
    await context.close()
  }
})
