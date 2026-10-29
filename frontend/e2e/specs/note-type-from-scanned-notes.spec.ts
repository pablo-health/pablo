// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Note types > From your notes, with a scanned PDF.
 *
 * This stack has no OCR service (ALLOW_DOCUMENT_AI_OCR=false), so a scan is
 * refused with a short reason instead of being proposed from empty text.
 * Reading a scan with OCR is covered by the backend tests against a stand-in
 * client. The fixture is the synthetic sample note with every page rendered to
 * an image (backend/tests/fixtures/build_sample_soap_note.py).
 */

import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

import { test, expect } from "../fixtures/auth"

const SCANNED = readFileSync(
  fileURLToPath(new URL("../../../backend/tests/fixtures/sample_soap_note_scanned.pdf", import.meta.url)),
)

test("a scanned note is refused with a reason when scans can't be read", async ({ signedInPage: page }) => {
  await page.goto("/dashboard/settings/note-types")
  await page.getByRole("button", { name: "Start from your notes" }).click()
  await page.getByLabel("Upload notes").setInputFiles({
    name: "scanned-note.pdf",
    mimeType: "application/pdf",
    buffer: SCANNED,
  })

  const derived = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/note-types/derive" && r.request().method() === "POST",
  )
  await page.getByRole("button", { name: "Propose a note type" }).click()
  expect((await derived).status()).toBe(422)

  await expect(page.getByRole("region", { name: "From your notes" }).getByRole("alert")).toHaveText(
    "Scanned PDFs can't be read here. Upload a Word or text version instead.",
  )
  await expect(page.getByLabel("Note type name")).toHaveCount(0)
})
