// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A SimplePractice export is a password-protected zip (the traditional zip
 * cipher; SimplePractice shows the password beside the download). The
 * captured fixture is committed unzipped, so the spec zips it with a
 * password the way the download is zipped, then uploads it without the
 * password, with the wrong one, and with the right one: the first two are
 * said plainly and the third previews the export.
 */

import { execFileSync } from "node:child_process"
import { mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

import { expect, test } from "../fixtures/auth"
import { MAIN_EXPORT } from "../fixtures/importArchive"

const IMPORT_URL = "/dashboard/settings/import"
const PASSWORD = "export-pw-e2e"

/** The fixture folder zipped with the traditional cipher, as the export downloads. */
function protectedExportZip(): { name: string; mimeType: string; buffer: Buffer } {
  const work = mkdtempSync(path.join(tmpdir(), "pablo-protected-export-"))
  try {
    const out = path.join(work, "export.zip")
    execFileSync(
      "zip",
      ["-q", "-r", "-P", PASSWORD, out, path.basename(MAIN_EXPORT), "-x", "*/README.md", "*.py"],
      { cwd: path.dirname(MAIN_EXPORT) },
    )
    return { name: "Export - Complete - e2e.zip", mimeType: "application/zip", buffer: readFileSync(out) }
  } finally {
    rmSync(work, { recursive: true, force: true })
  }
}

test("a protected export opens with its password, and says so without one", async ({
  signedInPage: page,
  api,
}) => {
  test.setTimeout(180_000)
  const zip = protectedExportZip()
  const status = await api.get<{ import_prompted_at: string | null }>("/api/users/me/status")
  if (status.import_prompted_at === null) {
    await page.goto("/onboarding/import-source")
    await page.getByRole("button", { name: "Continue" }).click()
    await page.waitForURL(`**${IMPORT_URL}`)
  } else {
    await page.goto(IMPORT_URL)
  }
  await expect(page.getByRole("heading", { name: "Import from SimplePractice" })).toBeVisible()

  // Without the password: told to enter it, nothing started.
  await page.getByTestId("import-archive-input").setInputFiles(zip)
  await expect(
    page.getByText("This export is protected with a password. Enter the one shown with the export."),
  ).toBeVisible()

  // The wrong password: said to be wrong, not a bad file.
  await page.getByLabel("Export password").fill("not-the-password")
  await page.getByTestId("import-archive-input").setInputFiles(zip)
  await expect(page.getByText("That password doesn't open this export.")).toBeVisible()

  // The right one: the export previews as the unprotected one does.
  await page.getByLabel("Export password").fill(PASSWORD)
  await page.getByTestId("import-archive-input").setInputFiles(zip)
  const summary = page.getByTestId("import-summary")
  await expect(summary).toBeVisible({ timeout: 90_000 })
  await expect(summary).toContainText("3 clients")
  await expect(summary).toContainText("15 notes")
})
