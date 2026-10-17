// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pablo's sidebar note: what it may say, and when it stays quiet.
 *
 * The failures these catch are the ones nobody sees in review: a line drifting
 * back to a morale tip that names nothing in the product, a line pointing at a
 * feature this deployment has switched off, and a downstream build's appended
 * or hidden lines being dropped by the merge.
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { PabloNote } from "../PabloNote"
import { pabloNoteLines, pickNoteLine, type PabloNoteLine } from "../pabloNoteLines"

const extensions = vi.hoisted(() => ({
  append: [] as PabloNoteLine[],
  hide: [] as string[],
}))
const runtime = vi.hoisted(() => ({
  config: {
    publicBookingEnabled: false,
    googleCalendarEnabled: false,
    passkeysEnabled: false,
  },
  features: {} as Record<string, boolean>,
  companion: false,
}))

vi.mock("../pabloNoteLines.extensions", () => ({ pabloNoteExtensions: extensions }))
vi.mock("@/lib/config", () => ({ useConfig: () => runtime.config }))
vi.mock("@/lib/featureGates", () => ({
  useFeaturePredicate: () => (name: string | undefined) => !name || Boolean(runtime.features[name]),
}))
vi.mock("@/lib/companion", () => ({ isCompanionAvailable: () => runtime.companion }))
vi.mock("@/hooks/usePeopleTerm", () => ({
  usePeopleTerm: () => ({ one: "client", many: "clients", One: "Client", Many: "Clients" }),
}))

/**
 * What a line may name. Every line has to point at something the reader can
 * find on screen; a line that names none of these is a morale line.
 */
const PRODUCT_NOUNS = [
  "Settings",
  "appointment",
  "Review",
  "SimplePractice",
  "Start session",
  "PHQ-9",
  "Google Calendar",
  "email",
  "superbill",
  "Passkeys",
  "note type",
  "Compliance",
  "dashboard",
  "chart",
  "packet",
]

/** Render the note once for every slot in the rotation; collect what showed. */
function shownIds(): Set<string> {
  const shown = new Set<string>()
  for (let day = 0; day < pabloNoteLines().length; day++) {
    vi.setSystemTime(day * 86_400_000 + 3_600_000)
    const { unmount } = render(<PabloNote />)
    const note = screen.queryByTestId("pablo-note")
    if (note) shown.add(note.getAttribute("data-line-id") ?? "")
    unmount()
  }
  return shown
}

describe("Pablo note lines", () => {
  afterEach(() => {
    vi.useRealTimers()
    extensions.append = []
    extensions.hide = []
    runtime.features = {}
    runtime.companion = false
    runtime.config = { publicBookingEnabled: false, googleCalendarEnabled: false, passkeysEnabled: false }
  })

  it("names a product noun in every line, with a unique id", () => {
    const lines = pabloNoteLines()

    expect(lines).toHaveLength(15)
    expect(new Set(lines.map((line) => line.id)).size).toBe(lines.length)
    for (const line of lines) {
      const named = PRODUCT_NOUNS.some((noun) => line.text.toLowerCase().includes(noun.toLowerCase()))
      expect(named, `"${line.text}" names nothing in the product`).toBe(true)
    }
  })

  it("never shows a line whose feature is off", () => {
    vi.useFakeTimers()
    const lines = pabloNoteLines()
    const gated = lines.filter((line) => line.requiresFlag).map((line) => line.id)
    expect(gated.length).toBeGreaterThan(0)

    const shown = shownIds()

    expect(shown.size).toBeGreaterThan(0)
    for (const id of gated) expect(shown.has(id), id).toBe(false)
  })

  it("shows a gated line once its feature is on, from the toggle the app reads for it", () => {
    vi.useFakeTimers()
    runtime.config = { publicBookingEnabled: true, googleCalendarEnabled: false, passkeysEnabled: false }
    runtime.features = { patient_portal: true }
    runtime.companion = true

    const shown = shownIds()

    expect(shown.has("self-booking")).toBe(true)
    expect(shown.has("intake-packet")).toBe(true)
    expect(shown.has("companion-start")).toBe(true)
    expect(shown.has("google-calendar")).toBe(false)
    expect(shown.has("passkeys")).toBe(false)
  })

  it("fills the people term into a line", () => {
    vi.useFakeTimers()
    runtime.config = { publicBookingEnabled: true, googleCalendarEnabled: false, passkeysEnabled: false }
    const lines = pabloNoteLines()
    vi.setSystemTime(lines.findIndex((line) => line.id === "self-booking") * 86_400_000)

    render(<PabloNote />)

    expect(screen.getByTestId("pablo-note")).toHaveTextContent(
      "Clients can book themselves from your page.",
    )
  })

  it("shows a line a downstream build appends, and drops one it hides", () => {
    vi.useFakeTimers()
    extensions.append = [{ id: "appended", text: "Settings > Appended holds the appended line." }]
    extensions.hide = ["drag-appointment"]
    const lines = pabloNoteLines()

    expect(lines.map((line) => line.id)).toContain("appended")
    expect(lines.map((line) => line.id)).not.toContain("drag-appointment")

    const shown = shownIds()
    expect(shown.has("appended")).toBe(true)
    expect(shown.has("drag-appointment")).toBe(false)
  })

  it("shows nothing when every line is gated off", () => {
    const lines = pabloNoteLines()
    expect(pickNoteLine(lines, () => false, 3)).toBeUndefined()
    expect(pickNoteLine([], () => true, 3)).toBeUndefined()
  })
})
