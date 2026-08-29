// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// @vitest-environment jsdom

/**
 * Pins the DOM implementation that HTML-sanitizing specs run against.
 *
 * The suite default is happy-dom, which is fast and complete enough for
 * rendering components. It is NOT complete enough for DOMPurify, which walks
 * and rewrites a document through APIs happy-dom only partly implements. Under
 * happy-dom, `DOMPurify.sanitize("<p>a</p>")` comes back as `a` — every element
 * stripped, whatever the input — while a planted `<script>` survives. A spec
 * written against that passes for the wrong reason and would not catch a
 * genuine sanitizer regression.
 *
 * So any spec that asserts on sanitized markup opts into jsdom with the
 * `@vitest-environment` pragma at the top of this file. These assertions are
 * the contract that pragma buys: real elements survive, script and event
 * handlers do not. If they ever fail, the pragma is missing or the environment
 * changed under us, and every sanitizing spec in the tree is suspect.
 */

import { describe, expect, it } from "vitest"
import DOMPurify from "dompurify"

describe("the jsdom test environment", () => {
  it("keeps ordinary markup instead of flattening it to text", () => {
    expect(DOMPurify.sanitize("<p>a</p>")).toBe("<p>a</p>")
    expect(DOMPurify.sanitize("<div><b>bold</b> and <i>italic</i></div>")).toBe(
      "<div><b>bold</b> and <i>italic</i></div>",
    )
  })

  it("lets DOMPurify remove a script element", () => {
    expect(DOMPurify.sanitize("<div>hi<script>alert(1)</script></div>")).toBe("<div>hi</div>")
  })

  it("lets DOMPurify remove an inline event handler", () => {
    const clean = DOMPurify.sanitize('<img src="x" onerror="alert(1)">')
    expect(clean).not.toContain("onerror")
    expect(clean).toContain("<img")
  })

  it("supports the document APIs DOMPurify needs", () => {
    expect(DOMPurify.isSupported).toBe(true)
  })
})
