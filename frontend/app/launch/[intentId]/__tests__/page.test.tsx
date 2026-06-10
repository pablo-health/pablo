// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import LaunchFallbackPage from "../page"

const clickThroughAnchor = vi.hoisted(() => vi.fn())

function renderLaunch(intentId: string) {
  return render(
    <LaunchFallbackPage params={Promise.resolve({ intentId })} />,
  )
}

vi.mock("@/lib/companionLaunch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/companionLaunch")>()
  return {
    ...actual,
    clickThroughAnchor: (...args: unknown[]) => clickThroughAnchor(...args),
  }
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("LaunchFallbackPage", () => {
  it("auto-fires the legacy scheme with the route intent id after a delay", async () => {
    renderLaunch("ix-1")

    // Wait out the ~1.2s auto-fire timer (real timers; the suspended
    // `use(params)` resolves on a microtask before the effect runs).
    await screen.findByRole("button", { name: /open pablo companion/i })
    await vi.waitFor(
      () => {
        expect(clickThroughAnchor).toHaveBeenCalledWith(
          "pablohealth://session/start?intent=ix-1",
        )
      },
      { timeout: 3000 },
    )
  })

  it("fires the legacy scheme when the manual button is clicked", async () => {
    const user = userEvent.setup()
    renderLaunch("ix-2")

    const button = await screen.findByRole("button", {
      name: /open pablo companion/i,
    })
    // Enabled once the route param Promise resolves.
    await vi.waitFor(() => expect(button).not.toBeDisabled())
    await user.click(button)

    expect(clickThroughAnchor).toHaveBeenCalledWith(
      "pablohealth://session/start?intent=ix-2",
    )
  })

  it("never displays patient data — only the opaque intent flow", async () => {
    renderLaunch("ix-3")
    expect(
      await screen.findByText(/opening pablo companion/i),
    ).toBeInTheDocument()
  })
})
