// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalOfferingCard tests — whether the practice offers the portal.
 *
 * Turning it on is one press. Turning it off asks first, because it ends
 * every client's access at once; cancelling leaves it on.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PortalOfferingCard } from "../PortalOfferingCard"
import { renderWithProviders } from "@/test/renderWithProviders"

const mockGet = vi.fn()
const mockSave = vi.fn()

vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockGet(...a),
  savePortalSettings: (...a: unknown[]) => mockSave(...a),
}))

function settingsAre(enabled: boolean) {
  mockGet.mockResolvedValue({ enabled, decided: true })
}

describe("PortalOfferingCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSave.mockImplementation((settings: { enabled: boolean }) => {
      settingsAre(settings.enabled)
      return Promise.resolve({ enabled: settings.enabled, decided: true })
    })
  })

  it("turns the portal on with one press", async () => {
    mockGet.mockResolvedValue({ enabled: false, decided: false })
    renderWithProviders(<PortalOfferingCard />)

    const toggle = await screen.findByRole("switch", { name: "Client portal" })
    expect(toggle).toHaveAttribute("aria-checked", "false")
    await userEvent.click(toggle)

    expect(mockSave).toHaveBeenCalledWith({ enabled: true })
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"))
  })

  it("asks before turning it off, and cancelling leaves it on", async () => {
    settingsAre(true)
    renderWithProviders(<PortalOfferingCard />)

    await userEvent.click(await screen.findByRole("switch", { name: "Client portal" }))

    expect(await screen.findByText("Turn off the client portal?")).toBeInTheDocument()
    expect(
      screen.getByText("Clients won't be able to sign in until you turn it back on."),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }))

    expect(mockSave).not.toHaveBeenCalled()
    expect(screen.getByRole("switch", { name: "Client portal" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
  })

  it("turns it off once confirmed", async () => {
    settingsAre(true)
    renderWithProviders(<PortalOfferingCard />)

    await userEvent.click(await screen.findByRole("switch", { name: "Client portal" }))
    await userEvent.click(await screen.findByRole("button", { name: "Turn off" }))

    expect(mockSave).toHaveBeenCalledWith({ enabled: false })
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Client portal" })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    )
  })

  it("renders nothing where the deployment has no portal", async () => {
    mockGet.mockRejectedValue(new Error("404"))
    const { container } = renderWithProviders(<PortalOfferingCard />)
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
