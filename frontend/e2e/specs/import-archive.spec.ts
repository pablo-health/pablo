// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice imports its SimplePractice export, the way a therapist does it.
 *
 * The archive is the committed, captured fixture, zipped at run time. The
 * journey starts at the onboarding question "Are you importing from another
 * EHR?", answers SimplePractice, and lands on the import screen. From there:
 *
 * - the export is uploaded and read in the background;
 * - the review asks all three kinds of question it can ask, with real data:
 *   a client already here (a Lulu Llama created first, with no birthday), a
 *   file that could belong to either of two clients named Pablo Bear — opened
 *   with View before it is assigned — and who wrote the notes;
 * - Import lands it, and the API is asked what the chart now holds;
 * - uploading the same export again adds nothing;
 * - undo takes the import back out, and keeps the Lulu who was here first.
 *
 * Every claim about the chart is read back through the API as the clinician,
 * not inferred from the screen.
 */

import { expect, test } from "../fixtures/auth"
import {
  MAIN_EXPORT,
  deletePatient,
  exportZip,
  noteCount,
  patientsNamed,
  undoAppliedImports,
} from "../fixtures/importArchive"
import { givePatient } from "../fixtures/scenarios"

const IMPORT_URL = "/dashboard/settings/import"

test("a SimplePractice export is imported, re-imported without duplicates, and undone", async ({
  api,
  signedInPage: page,
}) => {
  test.setTimeout(300_000)
  await undoAppliedImports(api)
  for (const leftover of await patientsNamed(api, "Llama")) await deletePatient(api, leftover.id)

  // A client already in the practice, with nothing to confirm she is the same person.
  const existing = await givePatient(api, { first_name: "Lulu", last_name: "Llama" })

  // --- onboarding: the question, answered SimplePractice -------------------
  // The question is asked once per user. A fresh stack (CI) has not asked it;
  // a reused local stack may have, and then the page hands straight back to
  // the wizard. Ask the API which it is rather than race the redirect.
  const status = await api.get<{ import_prompted_at: string | null }>("/api/users/me/status")
  if (status.import_prompted_at === null) {
    await page.goto("/onboarding/import-source")
    await expect(page.getByRole("heading", { name: "Are you importing from another EHR?" })).toBeVisible()
    await expect(page.getByRole("radio", { name: /SimplePractice/ })).toHaveAttribute("aria-checked", "true")
    await expect(page.getByRole("button", { name: "Skip" })).toBeVisible()
    await page.getByRole("button", { name: "Continue" }).click()
    await page.waitForURL(`**${IMPORT_URL}`)
  } else {
    // A reused local stack has already asked this user once; the question is asked once.
    test.info().annotations.push({ type: "note", description: "import question already answered on this stack" })
    await page.goto(IMPORT_URL)
  }

  // --- upload and review ---------------------------------------------------
  await expect(page.getByRole("heading", { name: "Import from SimplePractice" })).toBeVisible()
  await page.getByTestId("import-archive-input").setInputFiles(await exportZip(MAIN_EXPORT))

  const summary = page.getByTestId("import-summary")
  await expect(summary).toBeVisible({ timeout: 90_000 })
  await expect(summary).toContainText("3 clients")
  await expect(summary).toContainText("15 notes")
  await expect(page.getByText("Not coming across yet")).toBeVisible()

  const importButton = page.getByRole("button", { name: "Import", exact: true })
  await expect(importButton).toBeDisabled()
  await expect(page.getByText("3 questions to answer.")).toBeVisible()

  // A client already here: same person.
  await expect(page.getByText("You already have a client named Lulu Llama. Is this the same person?")).toBeVisible()
  await page.getByLabel("Same person — add to the existing client").check()

  // A file either Pablo Bear could own: read it, then say whose it is.
  const record = page.getByTestId("same-name-record")
  await expect(record).toHaveCount(1)
  const served = page.waitForResponse((r) => r.url().includes("/api/migration/runs/") && r.url().includes("/files?"))
  await record.getByRole("button", { name: "View" }).click()
  expect((await served).status()).toBe(200)
  await expect(record.getByTestId("import-file-viewer")).toBeVisible()
  await record.getByLabel(/Pablo A\. Bear · born 2025-01-01/).check()

  // Who wrote the notes.
  await page.getByLabel("Avery Provider is me").check()

  await expect(importButton).toBeEnabled()
  await importButton.click()
  const receipt = page.getByTestId("import-receipt")
  await expect(receipt).toBeVisible({ timeout: 90_000 })
  await expect(receipt).toContainText("Imported 3 clients and 15 notes.")

  // --- what the chart holds, read back through the API ---------------------
  const lulus = await patientsNamed(api, "Llama")
  expect(lulus.map((p) => p.id)).toEqual([existing.id]) // merged, not duplicated
  expect(await noteCount(api, existing.id)).toBe(6) // 2 progress, 2 psychotherapy, chart note, plan
  const bears = await patientsNamed(api, "Bear")
  expect(bears.map((p) => p.date_of_birth).sort()).toEqual(["2025-01-01", "2025-03-01"])
  const bornJanuary = bears.find((p) => p.date_of_birth === "2025-01-01")
  const bornMarch = bears.find((p) => p.date_of_birth === "2025-03-01")
  // Each Pablo Bear got the notes that name them: two visits each, a progress
  // and a psychotherapy note per visit, and the administrative note is January's.
  expect(await noteCount(api, bornJanuary!.id)).toBe(5)
  expect(await noteCount(api, bornMarch!.id)).toBe(4)

  // --- the same export again adds nothing ---------------------------------
  await page.getByTestId("import-archive-input").setInputFiles(await exportZip(MAIN_EXPORT))
  await expect(summary).toBeVisible({ timeout: 90_000 })
  await expect(summary).toContainText("Already here from an earlier import:")
  await expect(summary).toContainText("15 notes")
  expect((await patientsNamed(api, "Bear")).length).toBe(2)

  // --- history, and undo ---------------------------------------------------
  await page.reload()
  const rows = page.getByTestId("import-history-row")
  await expect(rows.first()).toBeVisible()
  const applied = rows.filter({ hasText: "Imported" })
  await expect(applied).toHaveCount(1)
  const undoCall = page.waitForResponse(
    (r) => r.url().endsWith("/undo") && r.request().method() === "POST",
  )
  await applied.getByRole("button", { name: "Undo this import" }).click()
  await applied.getByRole("button", { name: "Remove this import" }).click()
  const undone = await undoCall
  expect(undone.status()).toBe(200)
  const undoReport = (await undone.json()).report.undo
  expect(undoReport.removed.note).toBe(15)
  expect(undoReport.kept).toEqual([])
  // Other specs' runs may already read "Undone"; what matters is none still reads "Imported".
  await expect(rows.filter({ hasText: "Imported" })).toHaveCount(0)

  expect(await patientsNamed(api, "Bear")).toEqual([])
  expect((await patientsNamed(api, "Llama")).map((p) => p.id)).toEqual([existing.id])
  expect(await noteCount(api, existing.id)).toBe(0)

  // The re-upload's archive can be deleted before it expires on its own.
  const waiting = rows.filter({ hasText: "Ready to review" })
  await waiting.getByRole("button", { name: "Delete it now" }).click()
  await expect(waiting.getByRole("button", { name: "Delete it now" })).toHaveCount(0)

  await deletePatient(api, existing.id)
})
