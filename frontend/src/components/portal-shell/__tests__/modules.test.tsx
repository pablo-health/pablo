// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the engine mounts in the portal, and where it gets registered from.
 *
 * The order is product order — a patient meets the forms they were asked
 * for before messaging, and both before the appointments they come back to
 * check — and the registration has to run in the browser's
 * module graph. A
 * side-effect import from a server component fills the registry on the
 * server and leaves the browser's empty, which fails as slots that simply
 * never render: nothing throws, nothing logs, the shell just looks empty.
 * So the second test reads the shell's source and pins that the import
 * happens from the client component.
 */

import { readFileSync } from "fs"
import { join } from "path"
import { beforeEach, describe, expect, it, vi } from "vitest"

beforeEach(() => {
  vi.resetModules()
})

describe("portal modules", () => {
  it("registers forms, then messaging, then appointments", async () => {
    await import("../modules")
    const { getPortalSlots } = await import("../slots")

    expect(getPortalSlots().map((slot) => slot.id)).toEqual([
      "forms",
      "messaging",
      "appointments",
    ])
  })

  it("gates each slot on the module the deployment names", async () => {
    /**
     * The slot id and the module name are separate fields and only coincide
     * for two of the three: the forms slot belongs to the `intake` module,
     * which is the name the engine mounts routes under and the deployment
     * configures. Pinning the pairs here means a rename on either side has
     * to be deliberate.
     */
    await import("../modules")
    const { getPortalSlots } = await import("../slots")

    expect(getPortalSlots().map((slot) => [slot.id, slot.module])).toEqual([
      ["forms", "intake"],
      ["messaging", "messaging"],
      ["appointments", "appointments"],
    ])
  })

  it("is imported by the shell's client component", () => {
    const shell = readFileSync(join(__dirname, "..", "PortalShell.tsx"), "utf8")

    expect(shell).toContain('"use client"')
    expect(shell).toContain('import "./modules"')
  })
})
