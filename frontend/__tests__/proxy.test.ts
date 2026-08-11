// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { readFileSync } from "fs"
import { join } from "path"
import { describe, expect, it } from "vitest"

// THERAPY-hhmj — proxy.ts (renamed from middleware.ts per the Next 16
// convention) carries the matcher list that pablo-saas's Cloud Armor
// config mirrors (scripts/setup-cloud-armor.sh). Asserting on the file
// text, not the imported config, avoids pulling the auth provider chain
// into the test environment.

const proxySource = readFileSync(join(__dirname, "..", "proxy.ts"), "utf8")

describe("frontend/proxy.ts matcher contract", () => {
  it("still exempts /api/login", () => {
    expect(proxySource).toContain("/api/login")
  })

  it("still exempts /api/logout", () => {
    expect(proxySource).toContain("/api/logout")
  })

  it("still bypasses the Firebase auth helper (__/) prefix", () => {
    expect(proxySource).toContain("__/")
  })
})
