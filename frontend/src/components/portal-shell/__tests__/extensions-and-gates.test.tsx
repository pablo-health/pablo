// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Sections a deployment adds, and the gate a section can carry.
 *
 * A deployment lists extra sections in `modules.extensions.tsx`, which the
 * base build ships empty. `modules.tsx` registers them after the engine's,
 * and from there they are sections like any other: a tile on Home, a link
 * in the navigation, a page of their own. These tests go through the real
 * registration in `modules.tsx`, with the extension list mocked, so they
 * prove the route a deployment's file takes and not just the registry.
 *
 * A `Gate` wraps all three places at once: render nothing and the section
 * is gone from Home, the navigation and its page together.
 */

import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { PortalHome } from "../PortalHome"
import { PortalSection } from "../PortalSection"
import { PortalShell } from "../PortalShell"
import { SlotGate } from "../SlotGate"
import type { PortalSlot, PortalSlotProps } from "../slots"

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

// What the gate is told, and what it answers. Hoisted with the mock below.
const gate = vi.hoisted(() => ({
  open: true,
  seen: [] as Array<{ slug: string; sessionToken: string }>,
}))

vi.mock("../modules.extensions", () => {
  function Gate({ children, slug, sessionToken }: PortalSlotProps & { children: ReactNode }) {
    gate.seen.push({ slug, sessionToken })
    return gate.open ? <>{children}</> : null
  }
  const slots: PortalSlot[] = [
    {
      id: "added",
      label: "Added",
      Component: ({ sessionToken }) => <p>Added section for {sessionToken}</p>,
      Summary: () => <>Added summary</>,
    },
    {
      id: "gated",
      label: "Gated",
      Component: () => <p>Gated section</p>,
      Gate,
    },
  ]
  return { extensionPortalSlots: slots }
})

const SLUG = "example-therapy"

// Every engine module off, so only what the deployment added is on screen.
function capabilities(modules: Record<string, boolean> = {}) {
  return {
    ok: true,
    data: {
      practice: { display_name: "Example Therapy" },
      modules,
      auth_strength: "stepped_up",
    },
  }
}

function renderAt(path: string, child: ReactNode) {
  window.history.replaceState(null, "", path)
  return render(<PortalShell slug={SLUG}>{child}</PortalShell>)
}

beforeEach(() => {
  vi.clearAllMocks()
  gate.open = true
  gate.seen = []
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: SLUG, display_name: "Example Therapy" },
  })
  bootstrapSession.mockResolvedValue({ status: "active", sessionToken: "tok" })
  fetchCapabilities.mockResolvedValue(capabilities())
})

describe("a section the deployment adds", () => {
  it("is registered after the engine's sections, in the order listed", async () => {
    await import("../modules")
    const { getPortalSlots } = await import("../slots")

    expect(getPortalSlots().map((slot) => slot.id)).toEqual([
      "forms",
      "messaging",
      "appointments",
      "homework",
      "refills",
      "added",
      "gated",
    ])
  })

  it("gets a tile on Home, with its summary", async () => {
    renderAt(`/portal/${SLUG}`, <PortalHome />)

    const tile = await screen.findByTestId("portal-home-tile-added")
    expect(tile.getAttribute("href")).toBe(`/portal/${SLUG}/added`)
    expect(screen.getByTestId("portal-home-tile-added-summary").textContent).toBe("Added summary")
    // The engine's sections are off for this practice and stay hidden.
    expect(screen.queryByTestId("portal-home-tile-forms")).toBeNull()
  })

  it("gets a link in the navigation", async () => {
    renderAt(`/portal/${SLUG}`, <PortalHome />)

    const link = await screen.findByTestId("portal-shell-nav-added")
    expect(link.textContent).toBe("Added")
    expect(link.getAttribute("href")).toBe(`/portal/${SLUG}/added`)
  })

  it("gets a page of its own, handed the live session", async () => {
    renderAt(`/portal/${SLUG}/added`, <PortalSection id="added" />)

    const section = await screen.findByTestId("portal-section-added")
    expect(section.textContent).toBe("Added section for tok")
    expect(screen.getByTestId("portal-shell-nav-added").getAttribute("aria-current")).toBe("page")
    expect(replace).not.toHaveBeenCalled()
  })
})

describe("a section's gate", () => {
  it("shows the tile, the link and the page when it renders its children", async () => {
    renderAt(`/portal/${SLUG}`, <PortalHome />)

    expect(await screen.findByTestId("portal-home-tile-gated")).toBeTruthy()
    expect(screen.getByTestId("portal-shell-nav-gated")).toBeTruthy()
  })

  it("is handed the practice slug and the session token", async () => {
    renderAt(`/portal/${SLUG}`, <PortalHome />)

    await screen.findByTestId("portal-home-tile-gated")
    expect(gate.seen.length).toBeGreaterThan(0)
    expect(gate.seen.every((call) => call.slug === SLUG && call.sessionToken === "tok")).toBe(true)
  })

  it("hides the tile and the nav link when it renders nothing", async () => {
    gate.open = false
    renderAt(`/portal/${SLUG}`, <PortalHome />)

    // The ungated section is drawn, so the shell has finished drawing.
    await screen.findByTestId("portal-home-tile-added")
    expect(screen.getByTestId("portal-shell-nav-added")).toBeTruthy()
    expect(screen.queryByTestId("portal-home-tile-gated")).toBeNull()
    expect(screen.queryByTestId("portal-shell-nav-gated")).toBeNull()
    // No empty list item is left where the tile was.
    const tiles = screen.getByRole("list", { name: "Sections" })
    expect(tiles.querySelectorAll("li")).toHaveLength(1)
  })

  it("hides the page when it renders nothing", async () => {
    gate.open = false
    renderAt(`/portal/${SLUG}/gated`, <PortalSection id="gated" />)

    await screen.findByTestId("portal-shell-active")
    await waitFor(() => expect(gate.seen.length).toBeGreaterThan(0))
    expect(screen.queryByTestId("portal-section-gated")).toBeNull()
    expect(screen.queryByText("Gated section")).toBeNull()
  })

  it("shows the page when it renders its children", async () => {
    renderAt(`/portal/${SLUG}/gated`, <PortalSection id="gated" />)

    expect((await screen.findByTestId("portal-section-gated")).textContent).toBe("Gated section")
  })
})

describe("SlotGate", () => {
  const Component = () => null

  it("draws an ungated slot as it is, session or not", () => {
    render(
      <SlotGate slot={{ id: "plain", Component }} session={null}>
        <p>plain</p>
      </SlotGate>,
    )

    expect(screen.getByText("plain")).toBeTruthy()
  })

  it("draws nothing for a gated slot with no session to hand the gate", () => {
    const Gate = vi.fn(({ children }: PortalSlotProps & { children: ReactNode }) => <>{children}</>)
    render(
      <SlotGate slot={{ id: "gated", Component, Gate }} session={null}>
        <p>gated</p>
      </SlotGate>,
    )

    expect(screen.queryByText("gated")).toBeNull()
    expect(Gate).not.toHaveBeenCalled()
  })
})
