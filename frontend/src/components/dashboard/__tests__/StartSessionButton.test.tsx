// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StartSessionButton } from "../StartSessionButton"

const createLaunchIntent = vi.hoisted(() => vi.fn())
const clickThroughAnchor = vi.hoisted(() => vi.fn())
const armNoHandoffFallback = vi.hoisted(() => vi.fn(() => () => {}))

vi.mock("@/lib/api/devices", () => ({
  createLaunchIntent: (...args: unknown[]) => createLaunchIntent(...args),
}))

vi.mock("@/lib/companionLaunch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/companionLaunch")>()
  return {
    ...actual,
    clickThroughAnchor: (...args: unknown[]) => clickThroughAnchor(...args),
    armNoHandoffFallback: (...args: unknown[]) => armNoHandoffFallback(...args),
  }
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("StartSessionButton", () => {
  it("POSTs a launch intent and navigates to the verified launch_url on click", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-abc",
      launch_url: "https://app.pablo.health/launch/intent-abc",
      expires_in: 180,
    })
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-1" />)
    await user.click(screen.getByRole("button", { name: /start session/i }))

    expect(createLaunchIntent).toHaveBeenCalledWith("appt-1")
    expect(clickThroughAnchor).toHaveBeenCalledWith(
      "https://app.pablo.health/launch/intent-abc",
    )
    // No-handoff timer armed for the legacy fallback.
    expect(armNoHandoffFallback).toHaveBeenCalledTimes(1)
  })

  it("falls back to the legacy scheme with the SAME intent when no handoff happens", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-xyz",
      launch_url: "https://dev.pablo.health/launch/intent-xyz",
      expires_in: 180,
    })
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-2" />)
    await user.click(screen.getByRole("button", { name: /start session/i }))

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

  it("does nothing destructive when intent issuance fails", async () => {
    createLaunchIntent.mockRejectedValue(new Error("flag off"))
    const user = userEvent.setup()

    render(<StartSessionButton appointmentId="appt-3" />)
    await user.click(screen.getByRole("button", { name: /start session/i }))

    expect(clickThroughAnchor).not.toHaveBeenCalled()
    expect(armNoHandoffFallback).not.toHaveBeenCalled()
    // Button re-enables for a retry.
    expect(
      screen.getByRole("button", { name: /start session/i }),
    ).not.toBeDisabled()
  })
})
