// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The sidebar card slot: empty in the base build, and where a downstream
 * build's card lands when it fills it (after the navigation, before Pablo's
 * note). The failure this catches is the slot being dropped from `Sidebar` or
 * moved, which a downstream build would only notice as its card vanishing.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { Sidebar } from "../Sidebar"
import { SidebarCard } from "../SidebarCard"

const slot = vi.hoisted(() => ({ filled: false }))

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }))
vi.mock("@/hooks/usePeopleTerm", () => ({
  usePeopleTerm: () => ({ one: "client", many: "clients", One: "Client", Many: "Clients" }),
}))
vi.mock("../sidebarVisibility", () => ({ useNavVisibility: () => () => true }))
vi.mock("../HelpNavItem", () => ({ HelpNavItem: () => null }))
vi.mock("../NavBadge", () => ({ NavBadge: () => null }))
vi.mock("../SidebarFooter", () => ({ SidebarFooter: () => null }))
vi.mock("../PabloNote", () => ({ PabloNote: () => <div data-testid="pablo-note" /> }))
vi.mock("../SidebarCard", async (importOriginal) => {
  const original = await importOriginal<typeof import("../SidebarCard")>()
  return {
    SidebarCard: () => (slot.filled ? <div data-testid="sidebar-card" /> : original.SidebarCard()),
  }
})

describe("sidebar card slot", () => {
  it("renders nothing in the base build", () => {
    expect(SidebarCard()).toBeNull()
    slot.filled = false
    render(<Sidebar />)
    expect(screen.queryByTestId("sidebar-card")).toBeNull()
    expect(screen.getByTestId("pablo-note")).toBeInTheDocument()
  })

  it("puts a filled slot after the navigation and before Pablo's note", () => {
    slot.filled = true
    render(<Sidebar />)
    const nav = screen.getByRole("navigation", { name: "Main navigation" })
    const card = screen.getByTestId("sidebar-card")
    const note = screen.getByTestId("pablo-note")
    expect(nav.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(card.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
