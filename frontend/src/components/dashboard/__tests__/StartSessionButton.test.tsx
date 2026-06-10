// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StartSessionButton } from "../StartSessionButton"

const createLaunchIntent = vi.hoisted(() => vi.fn())
const clickThroughAnchor = vi.hoisted(() => vi.fn())
const armNoHandoffFallback = vi.hoisted(() =>
  vi.fn((_onNoHandoff: () => void): (() => void) => () => {}),
)

vi.mock("@/lib/api/devices", () => ({
  createLaunchIntent: (...args: unknown[]) => createLaunchIntent(...args),
}))

vi.mock("@/lib/companionLaunch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/companionLaunch")>()
  return {
    ...actual,
    clickThroughAnchor: (...args: unknown[]) => clickThroughAnchor(...args),
    armNoHandoffFallback: (onNoHandoff: () => void) =>
      armNoHandoffFallback(onNoHandoff),
  }
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("StartSessionButton", () => {
  it("prefetches the launch intent on hover and exposes it as the anchor href", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-abc",
      launch_url: "https://app.pablo.health/launch/intent-abc",
      expires_in: 180,
    })
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-1" />)
    const link = screen.getByRole("link", { name: /start session/i })
    // Inert until prefetched — no Universal Link href yet.
    expect(link).toHaveAttribute("href", "#")

    await user.hover(link)

    expect(createLaunchIntent).toHaveBeenCalledWith("appt-1")
    await waitFor(() =>
      expect(link).toHaveAttribute(
        "href",
        "https://app.pablo.health/launch/intent-abc",
      ),
    )
  })

  it("arms the no-handoff fallback on a real anchor click without re-POSTing", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-abc",
      launch_url: "https://app.pablo.health/launch/intent-abc",
      expires_in: 180,
    })
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-1" />)
    const link = screen.getByRole("link", { name: /start session/i })

    // Hover prefetches; the click then drives the real (verified-link) anchor.
    await user.hover(link)
    await waitFor(() => expect(link).not.toHaveAttribute("href", "#"))
    await user.click(link)

    // The verified link is the anchor's own default navigation — we do NOT
    // synthesize a click for it; we only arm the legacy fallback timer.
    expect(armNoHandoffFallback).toHaveBeenCalledTimes(1)
    // Exactly one intent issued — the prefetched one is reused.
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
  })

  it("falls back to the legacy scheme with the SAME intent when no handoff happens", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-xyz",
      launch_url: "https://dev.pablo.health/launch/intent-xyz",
      expires_in: 180,
    })
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-2" />)
    const link = screen.getByRole("link", { name: /start session/i })

    await user.hover(link)
    await waitFor(() => expect(link).not.toHaveAttribute("href", "#"))
    await user.click(link)

    // Simulate the no-handoff timer elapsing by invoking the callback the
    // component handed to armNoHandoffFallback.
    const onNoHandoff = armNoHandoffFallback.mock.calls[0][0] as () => void
    onNoHandoff()

    expect(clickThroughAnchor).toHaveBeenLastCalledWith(
      "pablohealth://session/start?intent=intent-xyz",
    )
    // Exactly one intent issued — the fallback reuses it, never re-POSTs.
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
  })

  it("does not orphan the fallback timer on a rapid second click", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-xyz",
      launch_url: "https://dev.pablo.health/launch/intent-xyz",
      expires_in: 180,
    })
    const cleanup = vi.fn()
    armNoHandoffFallback.mockReturnValue(cleanup)
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-2" />)
    const link = screen.getByRole("link", { name: /start session/i })

    await user.hover(link)
    await waitFor(() => expect(link).not.toHaveAttribute("href", "#"))
    await user.click(link)
    // A second click while the no-handoff window is still open is a no-op:
    // no new fallback armed, no new intent issued.
    await user.click(link)

    expect(armNoHandoffFallback).toHaveBeenCalledTimes(1)
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
    expect(cleanup).not.toHaveBeenCalled()
  })

  it("does nothing destructive when intent issuance fails on click", async () => {
    createLaunchIntent.mockRejectedValue(new Error("flag off"))
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-3" />)
    const link = screen.getByRole("link", { name: /start session/i })

    // No hover prefetch — fetch-on-click path, which rejects.
    await user.click(link)

    expect(clickThroughAnchor).not.toHaveBeenCalled()
    expect(armNoHandoffFallback).not.toHaveBeenCalled()
    // Anchor stays inert (still '#') and re-armable for a retry.
    expect(link).toHaveAttribute("href", "#")
    expect(link).not.toHaveAttribute("aria-disabled", "true")
  })
})
