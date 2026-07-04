// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * IdleTimeout controller: the backend idle clock is the source of truth.
 * Covers the restored-tab boot (dead session on mount), the server-driven
 * warning countdown, the keep-alive semantics of "Stay Signed In" and of
 * local activity, and the local-clock fallback when enforcement is off.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, act } from "@testing-library/react"

const { getSessionStatus, touchSession, handleTerminalAuthLogout, routerPush, signOut } =
  vi.hoisted(() => ({
    getSessionStatus: vi.fn(),
    touchSession: vi.fn(),
    handleTerminalAuthLogout: vi.fn(),
    routerPush: vi.fn(),
    signOut: vi.fn(),
  }))

vi.mock("@/lib/api/session", () => ({ getSessionStatus, touchSession }))
vi.mock("@/lib/api/client", () => ({ handleTerminalAuthLogout }))
vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ signOut }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}))

import { IdleTimeout } from "../IdleTimeout"

function sessionStatus(secondsRemaining: number, active = true) {
  return { enforced: true, active, seconds_remaining: secondsRemaining }
}

beforeEach(() => {
  vi.useFakeTimers()
  getSessionStatus.mockReset()
  touchSession.mockReset()
  handleTerminalAuthLogout.mockReset()
  routerPush.mockReset()
  signOut.mockReset().mockResolvedValue(undefined)
})

afterEach(() => {
  vi.useRealTimers()
})

async function renderAndSettleMount() {
  render(<IdleTimeout />)
  // Flush the mount-time validateSession promise.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

describe("IdleTimeout (server-enforced mode)", () => {
  it("boots through the shared forced-logout flow when the session is dead on mount", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(0, false))

    await renderAndSettleMount()

    expect(handleTerminalAuthLogout).toHaveBeenCalledWith("idle_timeout")
    expect(routerPush).not.toHaveBeenCalled() // hard boot, not the local path
  })

  it("renders nothing while the server clock is comfortably alive", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(900))

    await renderAndSettleMount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })

    expect(screen.queryByText("Session Expiring")).toBeNull()
    expect(handleTerminalAuthLogout).not.toHaveBeenCalled()
  })

  it("shows the warning with the SERVER's remaining time, not the local clock", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(90))

    await renderAndSettleMount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })

    // Local activity just happened (mount), yet the dialog shows because
    // the server says ~90s remain.
    expect(screen.getByText("Session Expiring")).toBeTruthy()
  })

  it("re-validates when the tab is restored (pageshow)", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(900))

    await renderAndSettleMount()
    expect(getSessionStatus).toHaveBeenCalledTimes(1)

    getSessionStatus.mockResolvedValue(sessionStatus(0, false))
    await act(async () => {
      fireEvent(window, new Event("pageshow"))
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(handleTerminalAuthLogout).toHaveBeenCalledWith("idle_timeout")
  })

  it("'Stay Signed In' touches the backend clock", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(90))
    touchSession.mockResolvedValue(sessionStatus(900))

    await renderAndSettleMount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })

    fireEvent.click(screen.getByText("Stay Signed In"))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })

    expect(touchSession).toHaveBeenCalledTimes(1)
    expect(screen.queryByText("Session Expiring")).toBeNull()
  })

  it("keeps a locally-active user alive server-side via the throttled touch", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(900))
    touchSession.mockResolvedValue(sessionStatus(900))

    await renderAndSettleMount()

    // Simulate steady typing: activity every 30s for >4 minutes.
    await act(async () => {
      for (let i = 0; i < 9; i++) {
        fireEvent(document, new Event("mousemove"))
        await vi.advanceTimersByTimeAsync(30_000)
      }
    })

    expect(touchSession).toHaveBeenCalled()
  })

  it("does NOT touch when the user is idle — checking must not extend the session", async () => {
    getSessionStatus.mockResolvedValue(sessionStatus(900))

    await renderAndSettleMount()
    // No activity events at all; just let polls run for 5 minutes.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000)
    })

    expect(getSessionStatus.mock.calls.length).toBeGreaterThan(1) // polling happened
    expect(touchSession).not.toHaveBeenCalled()
  })
})

describe("IdleTimeout (local fallback when enforcement is off)", () => {
  it("signs out via the provider after 15 minutes of local inactivity", async () => {
    getSessionStatus.mockResolvedValue({
      enforced: false,
      active: true,
      seconds_remaining: null,
    })

    await renderAndSettleMount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15 * 60_000 + 1_000)
    })

    expect(signOut).toHaveBeenCalled()
    expect(routerPush).toHaveBeenCalledWith("/login?reason=idle_timeout")
    expect(handleTerminalAuthLogout).not.toHaveBeenCalled()
  })
})
