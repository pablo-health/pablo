// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { expect, test } from "@playwright/test"
import { expectNoAccessibilityViolations } from "../fixtures/accessibility"
import { ApiClient, ensureEmulatorUser } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"

test.use({ bypassCSP: true })

test("the patient portal has no automatically detectable accessibility violations @a11y", async ({
  page,
}) => {
  await page.setViewportSize({ width: 412, height: 915 })
  const email = "e2e-accessibility@example.com"
  const password = "E2e-accessibility-password-long-enough"
  await ensureEmulatorUser(email, password)
  const api = await ApiClient.forUser(email, password)
  await api.put("/api/portal/settings", { enabled: true })
  const { slug } = await api.post<{ slug: string }>("/api/portal/practice-slug")

  await page.goto(`/portal/${slug}`)
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible()
  await expectNoAccessibilityViolations(page)

  const contact = givePortalContactDetails()
  const patient = await givePatient(api, contact)
  const invitation = await givePortalInvitation(api, patient.id, contact.email, contact.phone)
  await signInToPortal(page, invitation)
  await expectNoAccessibilityViolations(page)

  for (const section of ["Forms", "Messages", "Appointments", "Refills"]) {
    await page.getByRole("link", { name: section, exact: true }).first().click()
    await expectNoAccessibilityViolations(page)
  }
})
