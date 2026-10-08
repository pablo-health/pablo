// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Adjusting a built-in note type, end to end: start from the psychiatric
 * follow-up, hide one of its fields, add one of the practice's own, draft
 * the sample visit, and see both changes in the draft. Saved, the type
 * stores only its base and those changes, and the list says so.
 *
 * The stack drafts through its stand-in (NOTE_GENERATION_BASE_URL), which
 * answers every field it is asked for with "Stand-in draft for
 * <section>.<field>.", so the draft shows exactly the fields the server
 * resolved the type to.
 */

import { randomBytes } from "node:crypto"

import { test, expect } from "../fixtures/auth"

const BASE_LABEL = "Psychiatric follow-up (E/M + psychotherapy)"

type Saved = { key: string; based_on: { key: string; additions: number; hidden: number } | null }
type Stored = { spec: { base?: string; sections: unknown[]; patch?: { hide_fields: string[] } } }

test("a practice adjusts the follow-up: one field hidden, one added, both in the draft", async ({
  signedInPage: page,
  api,
}) => {
  const name = `Adjusted follow-up ${randomBytes(3).toString("hex")}`
  let slug: string | null = null
  try {
    await page.goto("/dashboard/settings/note-types")
    await page.getByRole("button", { name: `Start from ${BASE_LABEL}` }).click()
    await page.getByLabel("Note type name").fill(name)

    await page.getByRole("button", { name: "Hide Side effects" }).click()
    await expect(page.getByRole("button", { name: "Show Side effects" })).toBeVisible()
    await page.getByRole("button", { name: "Add a field to Plan" }).click()
    await page
      .getByRole("group", { name: "Your field 1 in Plan" })
      .getByLabel("Field name")
      .fill("Education provided")

    await expect(page.getByRole("radio", { name: "Sample visit" })).toHaveAttribute("aria-checked", "true")
    await page.getByLabel(/^Place of service/).selectOption("Telehealth")
    const previewed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/note-types/preview" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Draft a note" }).click()
    expect((await previewed).status()).toBe(200)
    const draft = page.getByTestId("try-it-draft")
    await expect(draft.getByText("Stand-in draft for plan.education_provided.")).toBeVisible()
    await expect(draft.getByText("Stand-in draft for subjective.chief_complaint.")).toBeVisible()
    await expect(draft.getByText("Stand-in draft for subjective.side_effects.")).toHaveCount(0)

    const saved = page.waitForResponse(
      (r) => new URL(r.url()).pathname.startsWith("/api/note-types/custom/") && r.request().method() === "PUT",
    )
    await page.getByRole("button", { name: "Save note type" }).click()
    const savedResponse = await saved
    expect(savedResponse.status(), await savedResponse.text()).toBe(200)
    const savedType = (await savedResponse.json()) as Saved
    slug = savedType.key.replace(/^custom\./, "")
    expect(savedType.based_on).toMatchObject({ key: "psychiatric_follow_up", additions: 1, hidden: 1 })

    await expect(page.getByText(`Based on ${BASE_LABEL}; 1 addition, 1 hidden`)).toBeVisible()
    const stored = await api.get<Stored>(`/api/note-types/${savedType.key}`)
    expect(stored.spec.base).toBe("psychiatric_follow_up")
    expect(stored.spec.sections).toEqual([])
    expect(stored.spec.patch?.hide_fields).toEqual(["subjective.side_effects"])
  } finally {
    if (slug) await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
