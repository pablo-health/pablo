// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A draft still arrives when the model that drafts notes is down. The stack
 * names a fallback model (AI_MODEL_FALLBACKS), and its stand-in for the
 * models (scripts/fake_llm.py) answers a transcript carrying the line below
 * from the fallback alone: every call to the first model fails with a 503.
 * So a draft can only appear here if drafting moved on to the fallback.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

const PRIMARY_DOWN = "The first drafting model is down for this session."

test("a transcript is drafted by the fallback model when the first one is down", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("button", { name: "New note" }).first().click()
  await page.getByRole("button", { name: /From a transcript/ }).click()
  await page.getByLabel(/Session Date & Time/).fill("2026-10-06T14:00")
  await page.locator("#transcript_file").setInputFiles({
    name: "session.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      `[00:00:05] Therapist: How have things been?\n[00:00:09] Client: ${PRIMARY_DOWN}`,
    ),
  })
  await page.getByRole("button", { name: "Upload & Generate SOAP" }).click()

  const notice = page.getByTestId("draft-notice")
  await expect(notice).toContainText("Draft ready")
  await notice.getByRole("link", { name: "Review draft" }).click()
  await expect(page).toHaveURL(/\/dashboard\/sessions\/[\w-]+$/)
  await expect(page.getByText(/Stand-in draft for/).first()).toBeVisible()
  await expect(page.getByText("Note generation failed")).toHaveCount(0)
})
