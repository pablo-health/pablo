// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The signed-in shell: which sections it draws, and signing out.
 *
 * Two things are under test and they pull in opposite directions, which is
 * why they share a file:
 *
 * * A module this practice does not have is NOT drawn — no section, no
 *   navigation entry — so a patient is never shown a part of a portal that
 *   does not exist for them.
 * * A capability document that never arrives draws EVERYTHING, because
 *   hiding a working portal over a cosmetic fetch would be the worse
 *   failure. The gate is the unmounted route, not this.
 *
 * Sign-out is here because it is the other thing the header does, and
 * because its own asymmetry matters: the server call is the act, the local
 * clear happens either way.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PortalShell } from "../PortalShell"
import { registerPortalSlot, resetPortalSlotsForTests } from "../slots"

const resolvePortalPractice = vi.fn()
const fetchCapabilities = vi.fn()
const bootstrapSession = vi.fn()
const redeemAndStore = vi.fn()
const signOutAndForget = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
  fetchCapabilities: (...args: unknown[]) => fetchCapabilities(...args),
}))

vi.mock("@/lib/portal-shell/session", () => ({
  bootstrapSession: (...args: unknown[]) => bootstrapSession(...args),
  redeemAndStore: (...args: unknown[]) => redeemAndStore(...args),
  signOutAndForget: (...args: unknown[]) => signOutAndForget(...args),
}))

const SLUG = "example-therapy"
const TOKEN = "live-session-token"

/** Every module the engine knows, all off. Spread and override per case. */
const ALL_OFF = {
  intake: false,
  messaging: false,
  documents: false,
  appointments: false,
  billing: false,
  chat: false,
}

function capabilities(modules: Partial<typeof ALL_OFF>, displayName: string | null = null) {
  return {
    ok: true,
    data: {
      practice: { display_name: displayName },
      modules: { ...ALL_OFF, ...modules },
      auth_strength: "stepped_up",
    },
  }
}

function registerTwoSlots(): void {
  registerPortalSlot({
    id: "intake",
    module: "intake",
    label: "Forms",
    Component: () => <p>Intake section</p>,
  })
  registerPortalSlot({
    id: "messaging",
    module: "messaging",
    label: "Messages",
    Component: () => <p>Messaging section</p>,
  })
}

async function renderSignedIn(): Promise<void> {
  render(<PortalShell slug={SLUG} />)
  await screen.findByTestId("portal-shell-active")
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, "", `/portal/${SLUG}`)
  resetPortalSlotsForTests()
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: SLUG, display_name: "Example Therapy" },
  })
  bootstrapSession.mockResolvedValue({ status: "active", sessionToken: TOKEN })
  fetchCapabilities.mockResolvedValue({ ok: false })
  signOutAndForget.mockResolvedValue({ revoked: true })
})

describe("what the shell draws", () => {
  /**
   * Both of these wait for the absence rather than checking it once.
   *
   * The shell draws every slot until the capability document arrives —
   * which is the behaviour two tests below pin deliberately — so there is a
   * first paint where the section IS on screen. Intake is present on both
   * sides of that, so waiting for it proves nothing about whether the
   * document has landed, and a single `queryByText` can read the paint
   * before the gate. It passed on a slow machine and failed on a fast one,
   * which is the signature.
   */
  it("renders only the slots whose module is enabled", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({ intake: true }))

    await renderSignedIn()

    await screen.findByText("Intake section")
    await waitFor(() => expect(screen.queryByText("Messaging section")).toBeNull())
  })

  it("shows a navigation entry only for the modules it drew", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({ intake: true }))

    await renderSignedIn()

    await screen.findByTestId("portal-shell-nav-intake")
    await waitFor(() => expect(screen.queryByTestId("portal-shell-nav-messaging")).toBeNull())
  })

  it("draws every enabled module, in registration order", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({ intake: true, messaging: true }))

    await renderSignedIn()

    await screen.findByTestId("portal-shell-nav-messaging")
    const labels = screen
      .getAllByRole("link")
      .map((link) => link.textContent)
      .filter((text) => text === "Forms" || text === "Messages")
    expect(labels).toEqual(["Forms", "Messages"])
  })

  it("falls back to the empty state when no module is enabled", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({}))

    await renderSignedIn()

    await screen.findByTestId("portal-shell-empty")
    expect(screen.queryByTestId("portal-shell-nav")).toBeNull()
  })

  it("draws no navigation at all when nothing is enabled", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({}))

    await renderSignedIn()

    await screen.findByTestId("portal-shell-empty")
    expect(screen.queryByText("Intake section")).toBeNull()
    expect(screen.queryByText("Messaging section")).toBeNull()
  })

  it("keeps every slot when the capability document cannot be fetched", async () => {
    /**
     * The failure direction that matters. Every module route is unmounted
     * when the deployment did not name it, so a slot drawn for a module
     * that is off meets its own error — whereas a shell that hid itself on
     * a failed fetch would take a working portal down.
     */
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue({ ok: false })

    await renderSignedIn()

    await screen.findByText("Intake section")
    expect(screen.getByText("Messaging section")).toBeTruthy()
  })

  it("renders a slot with no module whatever the document says", async () => {
    registerPortalSlot({ id: "notice", Component: () => <p>Always here</p> })
    fetchCapabilities.mockResolvedValue(capabilities({}))

    await renderSignedIn()

    await screen.findByText("Always here")
  })

  it("does not ask for capabilities before a session is live", async () => {
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug={SLUG} />)
    await screen.findByTestId("portal-shell-no-session")

    expect(fetchCapabilities).not.toHaveBeenCalled()
  })

  it("asks with the live session token", async () => {
    await renderSignedIn()

    await waitFor(() => expect(fetchCapabilities).toHaveBeenCalledWith(TOKEN))
  })

  it("takes the practice name from the document when it carries one", async () => {
    fetchCapabilities.mockResolvedValue(capabilities({}, "Meadowlark Counseling"))

    await renderSignedIn()

    await waitFor(() =>
      expect(screen.getByTestId("portal-shell-practice-name").textContent).toBe(
        "Meadowlark Counseling",
      ),
    )
  })

  it("keeps the resolved name when the document carries none", async () => {
    fetchCapabilities.mockResolvedValue(capabilities({}, null))

    await renderSignedIn()

    await screen.findByTestId("portal-shell-empty")
    expect(screen.getByTestId("portal-shell-practice-name").textContent).toBe("Example Therapy")
  })
})

describe("signing out", () => {
  it("offers Sign out only once a session is live", async () => {
    bootstrapSession.mockResolvedValue({ status: "none" })

    render(<PortalShell slug={SLUG} />)
    await screen.findByTestId("portal-shell-no-session")

    expect(screen.queryByTestId("portal-shell-sign-out")).toBeNull()
  })

  it("revokes the session and lands on the signed-out state", async () => {
    await renderSignedIn()

    await userEvent.click(screen.getByTestId("portal-shell-sign-out"))

    await screen.findByTestId("portal-shell-no-session")
    expect(signOutAndForget).toHaveBeenCalledWith(SLUG, TOKEN)
  })

  it("lands on the signed-out state even when the server call fails", async () => {
    /**
     * Somebody signing out on a shared computer is leaving. The useful
     * thing to do with a token whose revocation could not be confirmed is
     * to stop holding it — and to stop showing a screen they can no longer
     * act on.
     */
    signOutAndForget.mockResolvedValue({ revoked: false })

    await renderSignedIn()
    await userEvent.click(screen.getByTestId("portal-shell-sign-out"))

    await screen.findByTestId("portal-shell-no-session")
  })

  it("hides the sign-out control once it has been used", async () => {
    await renderSignedIn()

    await userEvent.click(screen.getByTestId("portal-shell-sign-out"))

    await screen.findByTestId("portal-shell-no-session")
    expect(screen.queryByTestId("portal-shell-sign-out")).toBeNull()
  })

  it("offers a way back in from the signed-out state", async () => {
    await renderSignedIn()
    await userEvent.click(screen.getByTestId("portal-shell-sign-out"))

    const link = await screen.findByTestId("portal-shell-recover-link")
    expect(link.getAttribute("href")).toBe(`/portal/${SLUG}/recover`)
  })

  it("offers the same way back from the expired state", async () => {
    /**
     * The shell cannot tell a lapsed session from a withdrawn one, and
     * neither can the recovery page — it answers the same way either way.
     * So the offer is the same, and a patient whose access really was
     * withdrawn simply gets nothing sent.
     */
    bootstrapSession.mockResolvedValue({ status: "expired" })

    render(<PortalShell slug={SLUG} />)

    const link = await screen.findByTestId("portal-shell-recover-link")
    expect(link.getAttribute("href")).toBe(`/portal/${SLUG}/recover`)
  })

  it("is reachable by keyboard with a visible name", async () => {
    await renderSignedIn()

    const control = screen.getByRole("button", { name: "Sign out" })
    control.focus()
    expect(document.activeElement).toBe(control)
  })

  it("names the navigation for a screen reader", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({ intake: true }))

    await renderSignedIn()

    const nav = await screen.findByRole("navigation", { name: "Portal sections" })
    expect(nav).toBeTruthy()
  })

  it("points each navigation entry at its own section", async () => {
    registerTwoSlots()
    fetchCapabilities.mockResolvedValue(capabilities({ intake: true, messaging: true }))

    await renderSignedIn()

    const entry = await screen.findByTestId("portal-shell-nav-messaging")
    expect(entry.getAttribute("href")).toBe("#portal-section-messaging")
    expect(document.getElementById("portal-section-messaging")).toBeTruthy()
  })
})
