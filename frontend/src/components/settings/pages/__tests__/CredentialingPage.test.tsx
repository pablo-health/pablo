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
import { findSettingsItem, settingsGroups } from "../../registry"
import { CREDENTIALING_SETTINGS_ID } from "../../paths"

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
  it("is reachable by its settings id", () => {
    expect(findSettingsItem(CREDENTIALING_SETTINGS_ID)?.page).toBe(CredentialingPage)
  })

  it("sits under Billing, with the rest of getting paid", () => {
    const billing = settingsGroups.find((group) => group.id === "billing")

    expect(billing?.items.map((item) => item.id)).toContain(CREDENTIALING_SETTINGS_ID)
  })

  it("is in no other group", () => {
    const groupsHolding = settingsGroups
      .filter((group) => group.items.some((item) => item.id === CREDENTIALING_SETTINGS_ID))
      .map((group) => group.id)

    expect(groupsHolding).toEqual(["billing"])
  })
})
