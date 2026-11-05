// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Header's account menu sign-out. It must end on a navigation to the logout
 * path whatever the provider sign-out does: a session that has gone stale
 * cannot complete an API call, and the click is the only way out the user
 * has. The query cache is still cleared first, so a page restored by Back
 * has nothing of the previous account in it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { HARD_SIGN_OUT_BUDGET_MS } from "@/lib/auth/hardSignOut"

const { signOut } = vi.hoisted(() => ({
  signOut: vi.fn(),
}))

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ id: "firebase", signOut }),
}))
vi.mock("@/components/theme/ThemeMenu", () => ({
  ThemeMenu: () => null,
}))

import { Header } from "../Header"

let assignSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  signOut.mockReset().mockResolvedValue(undefined)
  assignSpy = vi.fn()
  // happy-dom's real location.assign would attempt a navigation; replace it.
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { assign: assignSpy, href: "http://test/" },
  })
})

afterEach(() => {
  vi.useRealTimers()
})

function clickSignOut() {
  fireEvent.click(screen.getByLabelText("Open user menu"))
  fireEvent.click(screen.getByText("Sign out"))
}

describe("Header sign-out", () => {
  it("signs the provider out, then navigates to the logout path", async () => {
    renderWithProviders(<Header user={{ name: "Ann" }} />)

    clickSignOut()

    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/api/logout"))
    expect(signOut).toHaveBeenCalledWith({ wipePersisted: true })
  })

  it("clears the query cache before navigating", async () => {
    const { queryClient } = renderWithProviders(<Header user={{ name: "Ann" }} />)
    queryClient.setQueryData(["patients"], [{ id: "1" }])
    expect(queryClient.getQueryCache().getAll()).toHaveLength(1)

    clickSignOut()

    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/api/logout"))
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })

  it("still navigates when the provider sign-out fails", async () => {
    signOut.mockRejectedValue(new Error("network down"))
    renderWithProviders(<Header user={{ name: "Ann" }} />)

    clickSignOut()

    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/api/logout"))
  })

  it("still navigates when the provider sign-out never settles", async () => {
    vi.useFakeTimers()
    signOut.mockReturnValue(new Promise<void>(() => {}))
    renderWithProviders(<Header user={{ name: "Ann" }} />)

    clickSignOut()
    await vi.advanceTimersByTimeAsync(HARD_SIGN_OUT_BUDGET_MS - 1)
    expect(assignSpy).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)

    expect(assignSpy).toHaveBeenCalledWith("/api/logout")
  })
})
