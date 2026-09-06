// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"

// The merge slot a downstream build replaces. Hoisted so the factory can see it.
const extensions = vi.hoisted(() => ({
  overrides: {} as Record<string, Record<string, unknown>>,
  appendItems: [] as Record<string, unknown>[],
  appendGroups: [] as Record<string, unknown>[],
}))

vi.mock("../registry.extensions", () => ({ settingsExtensions: extensions }))

/**
 * The merge is what lets a downstream build add settings without copying the
 * page. If it silently drops the slot's groups, that build loses whole sections
 * of its nav and nothing fails loudly — the exact failure this replaced.
 */
describe("settings registry merge", () => {
  async function load() {
    vi.resetModules()
    return import("../registry")
  }

  it("ships the base groups when the slot is empty", async () => {
    const { settingsGroups } = await load()

    expect(settingsGroups.map((group) => group.id)).toEqual(["you", "practice", "billing"])
    expect(settingsGroups[0].items.map((item) => item.id)).toEqual(["profile", "appearance", "security"])
  })

  it("applies per-id overrides to base items without touching the rest", async () => {
    extensions.overrides = { profile: { label: "Your profile" } }

    const { findSettingsItem } = await load()

    expect(findSettingsItem("profile")?.label).toBe("Your profile")
    expect(findSettingsItem("appearance")?.label).toBe("Appearance")

    extensions.overrides = {}
  })

  it("places an appended group after the group it names, and at the end otherwise", async () => {
    extensions.appendGroups = [
      { id: "front", label: "Front office", items: [], insertAfter: "you" },
      { id: "later", label: "Later", items: [] },
      { id: "orphan", label: "Orphan", items: [], insertAfter: "nope" },
    ]

    const { settingsGroups } = await load()

    expect(settingsGroups.map((group) => group.id)).toEqual([
      "you",
      "front",
      "practice",
      "billing",
      "later",
      "orphan",
    ])

    extensions.appendGroups = []
  })

  it("honours insertBefore and insertAfter when appending items", async () => {
    extensions.appendGroups = []
    extensions.appendItems = [
      { id: "plan", label: "Plan", icon: () => null, page: () => null, desc: "", group: "billing", insertBefore: "superbills" },
      { id: "payments", label: "Patient payments", icon: () => null, page: () => null, desc: "", group: "billing", insertAfter: "plan" },
      { id: "notifications", label: "Notifications", icon: () => null, page: () => null, desc: "", group: "you" },
    ]

    const { settingsGroups } = await load()

    const billing = settingsGroups.find((group) => group.id === "billing")
    expect(billing?.items.map((item) => item.id)).toEqual(["plan", "payments", "superbills"])

    const you = settingsGroups.find((group) => group.id === "you")
    expect(you?.items.map((item) => item.id)).toEqual(["profile", "appearance", "security", "notifications"])

    extensions.appendItems = []
  })

  it("ignores an item aimed at a group this build does not have", async () => {
    extensions.appendItems = [
      { id: "ghost", label: "Ghost", icon: () => null, page: () => null, desc: "", group: "no-such-group" },
    ]

    const { findSettingsItem } = await load()

    expect(findSettingsItem("ghost")).toBeUndefined()

    extensions.appendItems = []
  })

  it("attaches the group label to every flattened item, for the header and search", async () => {
    const { settingsItems } = await load()

    expect(settingsItems.every((item) => item.groupLabel.length > 0)).toBe(true)
    expect(settingsItems.find((item) => item.id === "sessions")?.groupLabel).toBe("Practice")
  })

  it("gives every item a page, because the item carries its own", async () => {
    // The page used to live in a second map that could drift from this one.
    // Now a nav entry cannot exist without something to render.
    const { settingsItems } = await load()

    expect(settingsItems.every((item) => typeof item.page === "function")).toBe(true)
  })
})
