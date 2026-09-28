// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Home and the section pages under the shell.
 *
 * Home: the practice's welcome, from the capability document and nowhere
 * else, then one tile per section it serves. A section page: one section,
 * and back to Home for an address that names nothing this practice has.
 *
 * The address bar stands in for the router here — `usePathname` reads it —
 * so a test is on a page by putting its path there.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import type { PortalCapabilities } from "@/lib/portal-shell/api"
import { PortalHome } from "../PortalHome"
import { PortalSection } from "../PortalSection"
import { PortalShell } from "../PortalShell"
import { registerPortalSlot, resetPortalSlotsForTests } from "../slots"

const replace = vi.fn()

vi.mock("next/navigation", () => ({
  usePathname: () => window.location.pathname,
  useRouter: () => ({ replace }),
}))

const resolvePortalPractice = vi.fn()
const fetchCapabilities = vi.fn()
const bootstrapSession = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
  fetchCapabilities: (...args: unknown[]) => fetchCapabilities(...args),
}))

vi.mock("@/lib/portal-shell/session", () => ({
  bootstrapSession: (...args: unknown[]) => bootstrapSession(...args),
  redeemAndStore: vi.fn(),
  signOutAndForget: vi.fn(),
}))

const SLUG = "example-therapy"

function capabilities(overrides: Partial<PortalCapabilities> = {}) {
  return {
    ok: true,
    data: {
      practice: { display_name: "Example Therapy" },
      modules: { intake: true, refills: true },
      auth_strength: "stepped_up",
      ...overrides,
    },
  }
}

function registerSlots(): void {
  registerPortalSlot({
    id: "forms",
    module: "intake",
    label: "Forms",
    Component: () => <p>Forms section</p>,
    Summary: () => <>2 forms to complete</>,
  })
  registerPortalSlot({
    id: "refills",
    module: "refills",
    label: "Refills",
    Component: () => <p>Refills section</p>,
  })
}

function renderHome() {
  window.history.replaceState(null, "", `/portal/${SLUG}`)
  return render(
    <PortalShell slug={SLUG}>
      <PortalHome />
    </PortalShell>,
  )
}

function renderSection(id: string, path = `/portal/${SLUG}/${id}`) {
  window.history.replaceState(null, "", path)
  return render(
    <PortalShell slug={SLUG}>
      <PortalSection id={id} />
    </PortalShell>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  resetPortalSlotsForTests()
  registerSlots()
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: SLUG, display_name: "Example Therapy" },
  })
  bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "tok" })
  fetchCapabilities.mockResolvedValue(capabilities())
})

describe("Home", () => {
  it("shows the practice's welcome as written, line breaks kept", async () => {
    fetchCapabilities.mockResolvedValue(
      capabilities({ welcome: { heading: "Hello from us", body: "First line\nSecond line" } }),
    )

    renderHome()

    const heading = await screen.findByTestId("portal-home-welcome-heading")
    expect(heading.textContent).toBe("Hello from us")
    const body = screen.getByTestId("portal-home-welcome-body")
    expect(body.textContent).toBe("First line\nSecond line")
    expect(body.className).toContain("whitespace-pre-line")
  })

  it("greets with the practice's name when the server sends no welcome", async () => {
    renderHome()

    const heading = await screen.findByTestId("portal-home-welcome-heading")
    expect(heading.textContent).toBe("Welcome to Example Therapy")
    expect(screen.queryByTestId("portal-home-welcome-body")).toBeNull()
  })

  it("shows a skeleton, not a stand-in welcome, until the document arrives", async () => {
    fetchCapabilities.mockReturnValue(new Promise(() => {}))

    renderHome()

    expect(await screen.findByTestId("portal-home-skeleton")).toBeTruthy()
    expect(screen.queryByTestId("portal-home-welcome")).toBeNull()
    expect(screen.queryByText(/Welcome/)).toBeNull()
  })

  it("keeps the tiles, without a welcome, when the document never arrives", async () => {
    fetchCapabilities.mockResolvedValue({ ok: false })

    renderHome()

    expect(await screen.findByTestId("portal-home-tile-forms")).toBeTruthy()
    expect(screen.getByTestId("portal-home-tile-refills")).toBeTruthy()
    expect(screen.queryByTestId("portal-home-welcome")).toBeNull()
  })

  it("has one tile per section this practice serves, each linking to its page", async () => {
    fetchCapabilities.mockResolvedValue(capabilities({ modules: { intake: true, refills: false } }))

    renderHome()

    const tile = await screen.findByTestId("portal-home-tile-forms")
    expect(tile.getAttribute("href")).toBe(`/portal/${SLUG}/forms`)
    expect(tile.textContent).toContain("Forms")
    expect(screen.getByTestId("portal-home-tile-forms-summary").textContent).toBe(
      "2 forms to complete",
    )
    expect(screen.queryByTestId("portal-home-tile-refills")).toBeNull()
    expect(screen.queryByTestId("portal-shell-nav-refills")).toBeNull()
  })

  it("draws no section's content", async () => {
    renderHome()

    await screen.findByTestId("portal-home-tile-forms")
    expect(screen.queryByText("Forms section")).toBeNull()
    expect(screen.queryByText("Refills section")).toBeNull()
  })

  it("gives a tile with no summary its label alone", async () => {
    renderHome()

    const tile = await screen.findByTestId("portal-home-tile-refills")
    expect(tile.textContent).toBe("Refills")
    expect(screen.queryByTestId("portal-home-tile-refills-summary")).toBeNull()
  })

  it("links on the portal host's own form when that is the address in use", async () => {
    window.history.replaceState(null, "", `/${SLUG}`)
    render(
      <PortalShell slug={SLUG}>
        <PortalHome />
      </PortalShell>,
    )

    const tile = await screen.findByTestId("portal-home-tile-refills")
    expect(tile.getAttribute("href")).toBe(`/${SLUG}/refills`)
    expect(screen.getByTestId("portal-shell-nav-home").getAttribute("href")).toBe(`/${SLUG}`)
  })
})

describe("a section page", () => {
  it("shows that section alone and marks it current in the navigation", async () => {
    renderSection("refills")

    expect(await screen.findByText("Refills section")).toBeTruthy()
    expect(screen.queryByText("Forms section")).toBeNull()
    expect(screen.queryByTestId("portal-home")).toBeNull()
    expect(screen.getByTestId("portal-shell-nav-refills").getAttribute("aria-current")).toBe("page")
    expect(screen.getByTestId("portal-shell-nav-home").getAttribute("aria-current")).toBeNull()
  })

  it("stays put on a reload with a live session", async () => {
    renderSection("refills", `/${SLUG}/refills`)

    expect(await screen.findByText("Refills section")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("goes back to Home when its module is off", async () => {
    fetchCapabilities.mockResolvedValue(capabilities({ modules: { intake: true, refills: false } }))

    renderSection("refills")

    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/portal/${SLUG}`))
    expect(screen.queryByText("Refills section")).toBeNull()
  })

  it("does not draw a gated section before the document says it is on", async () => {
    fetchCapabilities.mockReturnValue(new Promise(() => {}))

    renderSection("refills")

    await screen.findByTestId("portal-shell-active")
    expect(screen.queryByText("Refills section")).toBeNull()
    expect(replace).not.toHaveBeenCalled()
  })

  it("goes back to Home for an id nothing registered", async () => {
    renderSection("billing", `/${SLUG}/billing`)

    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/${SLUG}`))
  })

  it("shows the same no-session card as Home when there is no session", async () => {
    bootstrapSession.mockResolvedValue({ status: "none" })

    renderSection("messaging")

    expect(await screen.findByTestId("portal-shell-no-session")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("does not offer the code form off Home, even with an invitation in the URL", async () => {
    bootstrapSession.mockResolvedValue({ status: "none" })

    renderSection("refills", `/portal/${SLUG}/refills#invite=tok-1`)

    expect(await screen.findByTestId("portal-shell-no-session")).toBeTruthy()
    expect(screen.queryByTestId("portal-shell-otp")).toBeNull()
  })
})
