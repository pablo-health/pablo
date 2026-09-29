// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientPortalPage tests — what waits while the practice's portal is off.
 *
 * The welcome and the invitation only matter once the practice offers the
 * portal, so they are greyed and unreachable until then. Forms stay open: a
 * practice can build them first.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"

import { PatientPortalPage } from "../PatientPortalPage"
import { renderWithProviders } from "@/test/renderWithProviders"

const mockSettings = vi.fn()

vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockSettings(...a),
}))

// The page's other cards fetch their own data; they are not what is under
// test here, so each is a marker.
vi.mock("../../intake/IntakeFormsCard", () => ({ IntakeFormsCard: () => <div>forms card</div> }))
vi.mock("../../intake/IntakeDocumentsCard", () => ({ IntakeDocumentsCard: () => null }))
vi.mock("../../intake/LicensedInstrumentsCard", () => ({ LicensedInstrumentsCard: () => null }))
vi.mock("../../intake/PortalWelcomeCard", () => ({
  PortalWelcomeCard: () => <button type="button">welcome card</button>,
}))
vi.mock("../../intake/InviteEmailCard", () => ({ InviteEmailCard: () => <div>invite card</div> }))

describe("PatientPortalPage", () => {
  beforeEach(() => vi.clearAllMocks())

  it("greys the welcome and the invitation while the portal is off", async () => {
    mockSettings.mockResolvedValue({ enabled: false, decided: true, modules: {} })
    renderWithProviders(<PatientPortalPage />)

    expect(await screen.findByTestId("portal-off-note")).toHaveTextContent(
      "Turn on the client portal to invite clients.",
    )
    const waiting = screen.getByTestId("portal-client-facing-settings")
    expect(waiting).toHaveAttribute("inert")
    expect(waiting).toHaveAttribute("aria-disabled", "true")
    expect(screen.getByText("forms card").closest("[inert]")).toBeNull()
  })

  it("leaves everything open once the portal is on", async () => {
    mockSettings.mockResolvedValue({ enabled: true, decided: true, modules: {} })
    renderWithProviders(<PatientPortalPage />)

    expect(await screen.findByRole("switch", { name: "Client portal" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.queryByTestId("portal-off-note")).not.toBeInTheDocument()
    expect(screen.getByTestId("portal-client-facing-settings")).not.toHaveAttribute("inert")
  })

  it("greys nothing before the answer arrives", async () => {
    mockSettings.mockReturnValue(new Promise(() => {}))
    renderWithProviders(<PatientPortalPage />)

    await waitFor(() => expect(mockSettings).toHaveBeenCalled())
    expect(screen.queryByTestId("portal-off-note")).not.toBeInTheDocument()
    expect(screen.getByTestId("portal-client-facing-settings")).not.toHaveAttribute("inert")
  })
})
