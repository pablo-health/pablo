// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Two clients share a name and neither has a birthday, so nothing in the
 * export says whose notes are whose. The import must not guess: every note
 * and the file are put in front of the therapist, who opens one, decides, and
 * each lands only on the client they chose.
 *
 * The archive is the committed capture from before either client had a
 * birthday or a middle initial (`simplepractice_export_same_name_no_dob`).
 */

import { expect, test } from "../fixtures/auth"
import {
  SAME_NAME_NO_DOB_EXPORT,
  exportZip,
  noteCount,
  patientsNamed,
  undoAppliedImports,
} from "../fixtures/importArchive"

test("notes that could belong to either same-named client land only where the therapist puts them", async ({
  api,
  signedInPage: page,
}) => {
  test.setTimeout(240_000)
  await undoAppliedImports(api)

  await page.goto("/dashboard/settings/import")
  await page.getByTestId("import-archive-input").setInputFiles(await exportZip(SAME_NAME_NO_DOB_EXPORT))
  await expect(page.getByTestId("import-summary")).toBeVisible({ timeout: 90_000 })

  await expect(page.getByText("Two clients are named Pablo Bear. Which one is each of these for?")).toBeVisible()
  const records = page.getByTestId("same-name-record")
  await expect(records).toHaveCount(3) // both notes of the visit, and the PDF upload
  await expect(page.getByText("4 questions to answer.")).toBeVisible()
  const importButton = page.getByRole("button", { name: "Import", exact: true })
  await expect(importButton).toBeDisabled()

  // The two clients are told apart by what the cards do carry.
  const second = /Pablo Bear · pablo\.bear2@example\.com/
  const progress = records.filter({ hasText: "Progress Note" })
  await progress.getByRole("button", { name: "View" }).click()
  await expect(progress.getByTestId("import-file-viewer")).toBeVisible()
  await progress.getByLabel(second).check()
  await records.filter({ hasText: "Psychotherapy Note" }).getByLabel(second).check()
  await records.filter({ hasText: "Sample upload.pdf" }).getByLabel("Don't import this").check()
  await page.getByLabel("Avery Provider is me").check()

  await expect(importButton).toBeEnabled()
  await importButton.click()
  await expect(page.getByTestId("import-receipt")).toContainText("Imported 2 clients and 2 notes.", {
    timeout: 90_000,
  })

  const bears = await patientsNamed(api, "Bear")
  expect(bears).toHaveLength(2)
  const chosen = bears.find((p) => p.email === "pablo.bear2@example.com")
  const other = bears.find((p) => p.email === "pablo.bear@example.com")
  expect(await noteCount(api, chosen!.id)).toBe(2)
  expect(await noteCount(api, other!.id)).toBe(0)

  await undoAppliedImports(api)
  expect(await patientsNamed(api, "Bear")).toEqual([])
})
