// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The user-menu merge slot. The base build ships it empty, so the account
 * menu is exactly what it was before the slot existed — an item appearing
 * here would appear in every downstream build's header.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { userMenuItems } from "../userMenuExtensions"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ signOut: vi.fn() }),
}))
vi.mock("@/components/theme/ThemeMenu", () => ({
  ThemeMenu: () => null,
}))

import { Header } from "../Header"

beforeEach(() => {
  vi.clearAllMocks()
})

describe("user-menu merge slot", () => {
  it("is empty in the base build", () => {
    expect(userMenuItems).toEqual([])
  })

  it("leaves Sign out as the only action in the menu", () => {
    renderWithProviders(<Header user={{ name: "Ann" }} />)
    fireEvent.click(screen.getByLabelText("Open user menu"))

    expect(screen.getByText("Sign out")).toBeInTheDocument()
    // No slot items, so the menu holds no links at all.
    expect(screen.queryAllByRole("link")).toHaveLength(0)
  })
})
