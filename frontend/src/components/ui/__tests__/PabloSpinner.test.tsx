// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { PabloSpinner } from "../PabloSpinner"

const realMatchMedia = window.matchMedia

function prefersReducedMotion(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)" ? matches : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

describe("PabloSpinner", () => {
  afterEach(() => {
    Object.defineProperty(window, "matchMedia", { writable: true, value: realMatchMedia })
  })

  it("names what Pablo is doing for a screen reader", () => {
    prefersReducedMotion(false)
    render(<PabloSpinner label="Reading your hours" />)

    expect(screen.getByRole("status")).toHaveTextContent("Reading your hours")
  })

  it("turns a ring around the bear", () => {
    prefersReducedMotion(false)
    render(<PabloSpinner label="Reading your hours" />)

    expect(screen.getByRole("status")).toHaveAttribute("data-motion", "animated")
    expect(screen.getByTestId("pablo-spinner-ring")).toHaveClass("animate-spin")
  })

  it("holds still for someone who has asked for less motion", () => {
    prefersReducedMotion(true)
    render(<PabloSpinner label="Reading your hours" />)

    expect(screen.getByRole("status")).toHaveAttribute("data-motion", "static")
    expect(screen.queryByTestId("pablo-spinner-ring")).toBeNull()
    expect(document.querySelector(".animate-spin")).toBeNull()
  })
})
