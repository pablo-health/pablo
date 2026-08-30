// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { SetupAside } from "../SetupAside"

describe("SetupAside", () => {
  it("is absent below the md breakpoint and shown at md and up", () => {
    render(<SetupAside img="/art.webp" caption="A caption" />)

    const caption = screen.getByText("A caption")
    const wrapper = caption.parentElement

    // `hidden` (display: none below md) paired with `md:flex` is how the
    // wizard's chrome hides the aside on mobile — jsdom doesn't evaluate
    // real media queries, so this asserts the responsive classes rather
    // than a computed style.
    expect(wrapper?.className).toContain("hidden")
    expect(wrapper?.className).toContain("md:flex")
  })

  it("renders the image and caption", () => {
    const { container } = render(<SetupAside img="/art.webp" caption="A caption" />)

    const img = container.querySelector("img")
    expect(img).toHaveAttribute("src", "/art.webp")
    expect(screen.getByText("A caption")).toBeInTheDocument()
  })
})
