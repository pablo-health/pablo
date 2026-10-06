// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Note types > From your notes, end to end: paste a sample note,
 * get a proposal in the editor, add a field for the passage it had no place
 * for, try a draft with it, and save.
 *
 * The stack's stand-in (NOTE_GENERATION_BASE_URL) proposes one fixed
 * follow-up note (Interval and Plan sections) and checks a sample by moving
 * each "Label: text" line into the field of that name, so a line with no such
 * label is what comes back as having no field. It drafts every field as
 * "Stand-in draft for <section>.<field>.", so the draft proves the added field
 * reached the model. The sample is synthetic.
 */

import { randomBytes } from "node:crypto"

import { test, expect } from "../fixtures/auth"

const STRAY = "The client brought a drawing from a weekend art class to show."
const SAMPLE = [
  "Interval history: Sleeping better since the last visit, appetite steady.",
  "Follow up: Return in four weeks.",
  STRAY,
].join("\n")

type Saved = { key: string; version: number; sections: Array<{ key: string; fields: Array<{ key: string; ai_hint: string }> }> }

test("a practice proposes a note type from a pasted note, adds a field for what didn't fit, tries it and saves it", async ({
  signedInPage: page,
  api,
}) => {
  const label = `Follow-up from notes ${randomBytes(3).toString("hex")}`
  let slug: string | null = null
  try {
    await page.goto("/dashboard/settings/note-types")
    await page.getByRole("button", { name: "Start from your notes" }).click()
    await expect(page.getByText(/Your notes are used for this and not kept\./)).toBeVisible()
    await page.getByLabel("Note 1").fill(SAMPLE)

    const derived = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/note-types/derive" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Propose a note type" }).click()
    const derivedResponse = await derived
    expect(derivedResponse.status(), await derivedResponse.text()).toBe(200)

    // The proposal is in the editor, unsaved.
    await expect(page.getByLabel("Note type name")).toHaveValue("Follow-up visit")
    await expect(page.getByRole("group", { name: "Section 1" }).getByLabel("Section name")).toHaveValue("Interval")

    // The line with no field is shown; adding a field for it puts a blank one in the last section.
    const passage = page.getByTestId("unplaced-passage")
    await expect(passage).toHaveCount(1)
    await expect(passage).toContainText(STRAY)
    await passage.getByRole("button", { name: "Add a field for this" }).click()
    await expect(passage).toContainText("Field added to Plan")
    const added = page.getByRole("group", { name: "Section 2" }).getByRole("group", { name: "Field 2" }).getByLabel("Field name")
    await expect(added).toBeFocused()
    await added.fill("Other observations")
    await page.getByLabel("Note type name").fill(label)

    // Try it with the added field.
    await page.getByLabel("Transcript").fill("Clinician: How have things been?\nClient: Better, mostly.")
    const previewed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/note-types/preview" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Draft a note" }).click()
    expect((await previewed).status()).toBe(200)
    await expect(page.getByTestId("try-it-draft").getByText("Stand-in draft for plan.other_observations.")).toBeVisible()

    const saved = page.waitForResponse(
      (r) => new URL(r.url()).pathname.startsWith("/api/note-types/custom/") && r.request().method() === "PUT",
    )
    await page.getByRole("button", { name: "Save note type" }).click()
    const savedResponse = await saved
    expect(savedResponse.status(), await savedResponse.text()).toBe(200)
    const savedType = (await savedResponse.json()) as Saved
    slug = savedType.key.replace(/^custom\./, "")
    await expect(page.getByRole("status")).toHaveText(`Saved ${label}, version 1.`)

    const stored = await api.get<Saved>(`/api/note-types/${encodeURIComponent(savedType.key)}`)
    expect(stored.sections.map((s) => s.key)).toEqual(["interval", "plan"])
    expect(stored.sections[1].fields.map((f) => f.key)).toEqual(["follow_up", "other_observations"])
    // The sample's own words are not part of the stored definition.
    expect(JSON.stringify(stored)).not.toContain("weekend art class")
  } finally {
    if (slug) await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
