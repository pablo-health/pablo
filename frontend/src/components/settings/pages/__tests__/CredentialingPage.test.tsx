// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings mounts the credentialing checklist; it does not rebuild it.
 *
 * The rule this guards is the one SetupSteps states out loud: two editors over
 * one record is how the two drift apart. A future hand reaching for "just a
 * small settings-only variant" should fail here rather than in a support
 * thread six months later, where one surface validates an NPI and the other
 * does not.
 *
 * Also guards the shape of the entry: a settings item under Billing, never a
 * nav item of its own. Credentialing had a place in the sidebar for a few
 * hours and it was taken back out deliberately.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { CredentialingPage } from "../CredentialingPage"
import { CredentialingStartPage } from "../CredentialingStartPage"
import { findSettingsItem, settingsGroups } from "../../registry"
import { CREDENTIALING_LEGACY_SETTINGS_ID, CREDENTIALING_SETTINGS_ID } from "../../paths"

vi.mock("@/components/credentialing/CredentialingWizard", () => ({
  CredentialingWizard: () => <div data-testid="credentialing-wizard" />,
}))

describe("the credentialing settings page", () => {
  it("mounts the shared wizard rather than a settings-only copy", () => {
    render(<CredentialingPage />)

    expect(screen.getByTestId("credentialing-wizard")).toBeInTheDocument()
  })
})

describe("where it lives", () => {
  it("is still reachable, under the deprecated id", () => {
    // Deprecated, not deleted: this is the only surface that reaches Tier 1
    // and Tier 2, and taking it away before its replacement exists would
    // remove working screens.
    expect(findSettingsItem(CREDENTIALING_LEGACY_SETTINGS_ID)?.page).toBe(CredentialingPage)
  })

  it("says deprecated in its label, where someone will actually read it", () => {
    expect(findSettingsItem(CREDENTIALING_LEGACY_SETTINGS_ID)?.label).toMatch(/deprecated/i)
  })

  it("no longer owns the plain credentialing id — the new flow does", () => {
    expect(findSettingsItem(CREDENTIALING_SETTINGS_ID)?.page).toBe(CredentialingStartPage)
  })

  it("both sit under Billing, with the rest of getting paid", () => {
    const billing = settingsGroups.find((group) => group.id === "billing")
    const ids = billing?.items.map((item) => item.id)

    expect(ids).toContain(CREDENTIALING_SETTINGS_ID)
    expect(ids).toContain(CREDENTIALING_LEGACY_SETTINGS_ID)
  })

  it("is in no other group", () => {
    const groupsHolding = settingsGroups
      .filter((group) =>
        group.items.some((item) =>
          [CREDENTIALING_SETTINGS_ID, CREDENTIALING_LEGACY_SETTINGS_ID].includes(item.id)
        )
      )
      .map((group) => group.id)

    expect(groupsHolding).toEqual(["billing"])
  })
})
