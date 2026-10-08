// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A document is part of a packet, so a packet's consent question can write
 * one in place. What this proves, end to end on the real stack:
 *
 *   - a link naming a packet (`?packet=<id>`) opens that packet's editor;
 *   - "Write a new one" on a consent question creates AND publishes the
 *     document, and points the question at it, without leaving the packet;
 *   - the document is an ordinary one: it shows in Documents in your
 *     packets, and the server holds it published under the same key the
 *     saved question carries.
 */

import { expect, test } from "../fixtures/auth"

interface IntakeTemplate {
  id: string
  name: string
  versions: { id: string; published_at: string | null }[]
}

interface IntakeVersionDetail {
  items: { key: string; item_type: string; config: Record<string, unknown> }[]
}

interface IntakeDocument {
  document_key: string
  title: string
  published_at: string | null
}

test.describe("A packet writes its own document", () => {
  test("a consent question writes, publishes and points at a new document", async ({
    page,
    api,
  }) => {
    const suffix = `${Date.now()}`
    const packetName = `Write-in packet ${suffix}`
    const documentTitle = `Group therapy agreement ${suffix}`
    const template = await api.post<IntakeTemplate>("/api/intake/templates", { name: packetName })

    // The link opens the packet: its name field is there without a click.
    await page.goto(`/dashboard/settings/portal?packet=${template.id}`)
    const packets = page.getByRole("region", { name: "Packets" })
    await expect(packets.getByLabel("Packet name")).toHaveValue(packetName)

    await packets.getByRole("combobox", { name: "Kind of question" }).click()
    await page.getByRole("option", { name: "Consent to sign", exact: true }).click()
    await packets.getByRole("button", { name: "Add question" }).click()

    await packets.getByRole("button", { name: "Write a new one" }).click()
    // Publish says what's missing rather than sitting greyed out.
    await packets.getByRole("button", { name: "Publish and use it" }).click()
    await expect(packets.getByText("Give the document a name.")).toBeVisible()

    await packets.getByLabel("Name", { exact: true }).fill(documentTitle)
    await packets.getByLabel("What they read").fill("We meet weekly. What is said in group stays in group.")
    await packets.getByRole("button", { name: "Publish and use it" }).click()
    await expect(packets.getByRole("button", { name: "Write a new one" })).toBeVisible()

    await packets.getByRole("button", { name: "Save", exact: true }).click()

    // On the server: the question points at a document that is published.
    const draftUrl = `/api/intake/templates/${template.id}/versions/${template.versions[0].id}`
    await expect
      .poll(async () => {
        const items = (await api.get<IntakeVersionDetail>(draftUrl)).items
        return items.filter((i) => i.item_type === "consent_document").length
      })
      .toBe(1)
    const consent = (await api.get<IntakeVersionDetail>(draftUrl)).items.find(
      (i) => i.item_type === "consent_document",
    )
    const published = await api.get<IntakeDocument[]>("/api/intake/documents?published_only=true")
    const written = published.find((d) => d.title === documentTitle)
    expect(written, "the written document is published").toBeDefined()
    expect(consent?.config.document_key).toBe(written?.document_key)

    // And it is an ordinary document, listed with the others.
    const documents = page.getByRole("region", { name: "Documents in your packets" })
    await expect(documents.getByRole("button", { name: new RegExp(documentTitle) })).toBeVisible()
  })
})
