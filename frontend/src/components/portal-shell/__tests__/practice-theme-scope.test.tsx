// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal in a practice's theme, and — the case that matters most — the
 * portal exactly as it is without one.
 */

import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { PracticeThemeScope } from "../PracticeThemeScope"

const THEME = {
  colors: { accent: "#24504c", accentText: "#ffffff", background: "#fbf8f3" },
  fonts: { heading: "Fraunces", body: "Inter" },
  radius: "lg" as const,
}

function Portal() {
  return <div data-testid="portal">The portal</div>
}

describe("PracticeThemeScope", () => {
  it("adds nothing at all without a theme", () => {
    const { container } = render(
      <PracticeThemeScope theme={null}>
        <Portal />
      </PracticeThemeScope>,
    )

    expect(container.innerHTML).toBe('<div data-testid="portal">The portal</div>')
    expect(document.querySelector("style")).toBeNull()
    expect(document.querySelector("[data-practice-theme]")).toBeNull()
  })

  it("puts the theme's tokens on an element around the portal", () => {
    render(
      <PracticeThemeScope theme={THEME}>
        <Portal />
      </PracticeThemeScope>,
    )

    const scope = screen.getByTestId("practice-theme")
    expect(scope).toHaveAttribute("data-practice-theme")
    expect(scope).toContainElement(screen.getByTestId("portal"))
    const css = scope.querySelector("style")?.textContent ?? ""
    expect(css).toContain("--primary:#24504c")
    expect(css).toContain("--radius:1rem")
    // Quotes stay quotes, so the font name reaches the browser intact.
    expect(css).toContain('font-family:"Inter", system-ui, sans-serif')
  })
})
