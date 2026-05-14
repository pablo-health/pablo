// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ScopeFooter (§13.7) — static disclaimer below the composer.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"

import { SCOPE_FOOTER_TEXT, ScopeFooter } from "../ScopeFooter"

describe("ScopeFooter", () => {
  it("renders the exact §13.7 copy verbatim", () => {
    render(<ScopeFooter />)
    expect(screen.getByText(SCOPE_FOOTER_TEXT)).toBeInTheDocument()
  })

  it("mentions all three required clauses (summary, not a clinical tool, PHI scope)", () => {
    render(<ScopeFooter />)
    const node = screen.getByText(SCOPE_FOOTER_TEXT)
    expect(node.textContent).toMatch(/summarizes chart context/i)
    expect(node.textContent).toMatch(/not a clinical decision tool/i)
    expect(node.textContent).toMatch(/PHI stays in this practice/i)
    expect(node.textContent).toMatch(/purged on delete/i)
  })

  it("uses the data-slot hook the panel keys off", () => {
    const { container } = render(<ScopeFooter />)
    expect(
      container.querySelector("[data-slot='chat-scope-footer']"),
    ).not.toBeNull()
  })

  it("does not render any link (no anchor element)", () => {
    const { container } = render(<ScopeFooter />)
    expect(container.querySelector("a")).toBeNull()
  })
})
