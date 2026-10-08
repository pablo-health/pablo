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
    const packets = page.getByRole("region", { name: "Packets", exact: true })
    await expect(packets.getByLabel("Packet name")).toHaveValue(packetName)

    // A document goes in through its own button, not as a kind of question.
    await packets.getByRole("button", { name: "Add a document" }).click()
    await packets.getByRole("button", { name: "Write a new one" }).click()
    // Publish says what's missing rather than sitting greyed out.
    await packets.getByRole("button", { name: "Publish and use it" }).click()
    await expect(packets.getByText("Give the document a name.")).toBeVisible()

    await packets.getByLabel("Document name", { exact: true }).fill(documentTitle)
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

    // And it is an ordinary document, listed with the others once the list is opened.
    const documents = page.getByRole("region", { name: "Documents in your packets" })
    await documents.getByRole("button", { name: /^Show \d+ documents?$/ }).click()
    await expect(documents.getByRole("button", { name: new RegExp(documentTitle) })).toBeVisible()
  })

  test("a consent question can ask for an earlier published wording, and that is what publishing pins", async ({
    page,
    api,
  }) => {
    const suffix = `${Date.now()}`
    const title = `Fee agreement ${suffix}`
    // Two published wordings of one document.
    const draft = await api.post<IntakeDocumentRow>("/api/intake/documents", {
      title,
      body_markdown: "The fee is $150 a session.",
    })
    const first = await api.post<IntakeDocumentRow>(`/api/intake/documents/${draft.id}/publish`)
    const next = await api.post<IntakeDocumentRow>(`/api/intake/documents/${first.id}/new-version`)
    await api.put(`/api/intake/documents/${next.id}`, { body_markdown: "The fee is $165 a session." })
    const second = await api.post<IntakeDocumentRow>(`/api/intake/documents/${next.id}/publish`)
    expect(second.version).toBe(2)

    const template = await api.post<IntakeTemplate>("/api/intake/templates", {
      name: `Wording packet ${suffix}`,
    })
    await page.goto(`/dashboard/settings/portal?packet=${template.id}`)
    const packets = page.getByRole("region", { name: "Packets", exact: true })

    await packets.getByRole("button", { name: "Add a document" }).click()
    await packets.getByRole("button", { name: title, exact: true }).click()
    // On the packet under its own name, so which document it is reads at a glance.
    await expect(packets.getByRole("combobox", { name: "Which document" })).toContainText(title)

    // Newest unless told otherwise; told otherwise here.
    const wording = packets.getByRole("combobox", { name: "Which wording" })
    await expect(wording).toContainText("The newest when you publish (now version 2)")
    await wording.click()
    await page.getByRole("option", { name: /^Version 1, published/ }).click()

    await packets.getByRole("button", { name: "Save", exact: true }).click()
    const draftUrl = `/api/intake/templates/${template.id}/versions/${template.versions[0].id}`
    await expect
      .poll(async () => (await api.get<IntakeVersionDetail>(draftUrl)).items.length)
      .toBeGreaterThan(0)
    await packets.getByRole("button", { name: "Publish" }).click()

    // The published packet asks people to sign version 1, by its exact id.
    await expect
      .poll(async () => {
        const items = (await api.get<IntakeVersionDetail>(draftUrl)).items
        return items.find((i) => i.item_type === "consent_document")?.config.document_version_id
      })
      .toBe(first.id)
  })
})

interface IntakeDocumentRow {
  id: string
  version: number
  published_at: string | null
}
