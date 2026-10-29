// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A person with two packets can tell them apart in their portal.
 *
 * Each packet carries a title written for the person filling it in, set
 * from the Packets card, and the portal names each row by it. The practice's
 * own name for a packet is its private label: it never reaches the portal,
 * neither on screen nor in what the portal is sent.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePortalInvitation, openPortalSection, signInToPortal } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"

interface IntakeVersion {
  id: string
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  client_title: string | null
  versions: IntakeVersion[]
}

/** A one-question packet under a private name, published. */
async function publishPacket(api: ApiClient, name: string): Promise<IntakeTemplate> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", { name })
  const draftId = template.versions[0].id
  await api.put(`/api/intake/templates/${template.id}/versions/${draftId}/items`, {
    items: [{ key: "note", item_type: "free_text", label: "Anything you'd like us to know?", config: {} }],
  })
  const published = await api.post<IntakeVersion>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at).not.toBeNull()
  return { ...template, versions: [published] }
}

test("two packets reach one client under the titles written for them @portal", async ({ api, page }) => {
  const stamp = Date.now().toString(36)
  const firstPrivate = `New client intake ${stamp}`
  const secondPrivate = `Annual refresh ${stamp}`
  const firstTitle = `Before your first visit ${stamp}`
  const secondTitle = `Yearly check-in ${stamp}`

  const first = await publishPacket(api, firstPrivate)
  const second = await publishPacket(api, secondPrivate)

  // The first title is written where a practice writes it: the Packets card.
  await page.goto(`/dashboard/settings/portal?packet=${first.id}`)
  const packets = page.getByRole("region", { name: "Packets", exact: true })
  await packets.getByLabel("Title clients see").fill(firstTitle)
  await packets.getByRole("button", { name: "Save title" }).click()
  await expect
    .poll(async () => (await api.get<IntakeTemplate>(`/api/intake/templates/${first.id}`)).client_title)
    .toBe(firstTitle)
  // The second through the API the card uses.
  await api.patch(`/api/intake/templates/${second.id}`, { client_title: secondTitle })

  const email = `titles-${stamp}@example.com`
  const phone = `+1555${`${Date.now()}`.slice(-7)}`
  const patient = await givePatient(api, { email, phone, date_of_birth: "1990-06-01" })
  for (const packet of [first, second]) {
    await api.post(`/api/patients/${patient.id}/intake-assignments`, {
      version_id: packet.versions[0].id,
    })
  }

  // What the portal is sent never carries the practice's own names.
  const listed = page.waitForResponse((r) => r.url().includes("/api/patient/intake/assignments"))
  await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
  await openPortalSection(page, "forms")
  const body = await (await listed).text()
  expect(body).not.toContain(firstPrivate)
  expect(body).not.toContain(secondPrivate)
  expect(body).not.toContain("packet_name")

  const titles = page.getByTestId("forms-list-title")
  await expect(titles).toHaveCount(2)
  // Order is the server's (newest first); what matters is that each is there.
  expect((await titles.allInnerTexts()).sort()).toEqual([firstTitle, secondTitle].sort())
  await expect(page.getByText(firstPrivate)).toHaveCount(0)
  await expect(page.getByText(secondPrivate)).toHaveCount(0)
})
