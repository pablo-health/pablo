// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Header's account menu sign-out. The provider call and the redirect were
 * already covered elsewhere; what matters here is that a soft sign-out
 * (client-side navigation, no page reload) doesn't leave the previous
 * account's query results sitting in the cache for whoever logs in next.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"

const { routerPush, signOut } = vi.hoisted(() => ({
  routerPush: vi.fn(),
  signOut: vi.fn(),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}))
vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ signOut }),
}))
vi.mock("@/components/theme/ThemeMenu", () => ({
  ThemeMenu: () => null,
}))

import { Header } from "../Header"

beforeEach(() => {
  routerPush.mockReset()
  signOut.mockReset().mockResolvedValue(undefined)
})

describe("Header sign-out", () => {
  it("clears the query cache before navigating to /login", async () => {
    const { queryClient } = renderWithProviders(<Header user={{ name: "Ann" }} />)
    queryClient.setQueryData(["patients"], [{ id: "1" }])
    expect(queryClient.getQueryCache().getAll()).toHaveLength(1)

    fireEvent.click(screen.getByLabelText("Open user menu"))
    fireEvent.click(screen.getByText("Sign out"))

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/login"))
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })
})
