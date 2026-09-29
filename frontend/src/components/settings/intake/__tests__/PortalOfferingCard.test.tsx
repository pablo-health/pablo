// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalOfferingCard tests — whether the practice offers the portal, and
 * what clients can do in it.
 *
 * Turning it on is one press. Turning it off asks first, because it ends
 * every client's access at once; cancelling leaves it on. Each part can be
 * turned off on its own, never the last one, and only while the portal is
 * on. The Appointments row says where booking stands, from the scheduling
 * policy, rather than keeping a second switch for it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PortalOfferingCard } from "../PortalOfferingCard"
import { renderWithProviders } from "@/test/renderWithProviders"

const mockGet = vi.fn()
const mockSave = vi.fn()
const mockPolicy = vi.fn()

vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockGet(...a),
  savePortalSettings: (...a: unknown[]) => mockSave(...a),
}))

vi.mock("@/lib/api/schedulingPolicy", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/schedulingPolicy")>()),
  getSchedulingPolicy: (...a: unknown[]) => mockPolicy(...a),
}))

type Modules = Record<string, boolean>
const ALL_ON: Modules = { intake: true, messaging: true, appointments: true, refills: true }

let current = { enabled: false, decided: false, modules: ALL_ON }

function settingsAre(enabled: boolean, modules: Modules = ALL_ON) {
  current = { enabled, decided: true, modules }
}

describe("PortalOfferingCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    current = { enabled: false, decided: false, modules: ALL_ON }
    mockGet.mockImplementation(() => Promise.resolve(current))
    mockSave.mockImplementation((change: { enabled?: boolean; modules?: Modules }) => {
      current = {
        enabled: change.enabled ?? current.enabled,
        decided: true,
        modules: { ...current.modules, ...change.modules },
      }
      return Promise.resolve(current)
    })
    mockPolicy.mockResolvedValue({ self_book_existing: false, self_book_mode: "request" })
  })

  it("turns the portal on with one press", async () => {
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

  it("lists each part by its plain name, in the order clients meet them", async () => {
    settingsAre(true)
    renderWithProviders(<PortalOfferingCard />)

    const rows = await screen.findByTestId("portal-modules")
    const names = Array.from(rows.querySelectorAll("[role=switch]")).map((el) =>
      el.getAttribute("aria-label"),
    )
    expect(names).toEqual(["Forms", "Messages", "Appointments", "Refill requests"])
  })

  it("turns one part off on its own", async () => {
    settingsAre(true)
    renderWithProviders(<PortalOfferingCard />)

    const appointments = await screen.findByRole("switch", { name: "Appointments" })
    await userEvent.click(appointments)

    expect(mockSave).toHaveBeenCalledWith({ modules: { appointments: false } })
    await waitFor(() => expect(appointments).toHaveAttribute("aria-checked", "false"))
  })

  it("keeps the last part on", async () => {
    settingsAre(true, { intake: false, messaging: true, appointments: false, refills: false })
    renderWithProviders(<PortalOfferingCard />)

    expect(await screen.findByRole("switch", { name: "Messages" })).toBeDisabled()
    expect(screen.getByRole("switch", { name: "Forms" })).not.toBeDisabled()
  })

  it("leaves the parts alone while the portal is off", async () => {
    settingsAre(false)
    renderWithProviders(<PortalOfferingCard />)

    expect(await screen.findByRole("switch", { name: "Refill requests" })).toBeDisabled()
  })

  it.each([
    [{ self_book_existing: false, self_book_mode: "request" }, "Clients can see their appointments."],
    [{ self_book_existing: true, self_book_mode: "request" }, "Clients can also request appointments."],
    [{ self_book_existing: true, self_book_mode: "auto" }, "Clients can also book appointments."],
  ])("says where booking stands under Appointments (%o)", async (policy, sentence) => {
    settingsAre(true)
    mockPolicy.mockResolvedValue(policy)
    renderWithProviders(<PortalOfferingCard />)

    const line = await screen.findByTestId("portal-booking-line")
    await waitFor(() => expect(line).toHaveTextContent(sentence))
    expect(screen.getByRole("link", { name: "Booking settings" })).toHaveAttribute(
      "href",
      "/dashboard/settings/scheduling",
    )
  })

  it("renders nothing where the deployment has no portal", async () => {
    mockGet.mockRejectedValue(new Error("404"))
    const { container } = renderWithProviders(<PortalOfferingCard />)
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
