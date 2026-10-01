// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The item-renderer registry: built-in kinds have their own, other code adds
 * kinds with `registerInboxItemRenderer`, and a kind nobody registered still
 * shows through the generic card.
 */

import { describe, it, expect } from "vitest"
import { screen } from "@testing-library/react"
import { renderWithProviders as render } from "@/test/renderWithProviders"
import type { InboxItem } from "@/lib/api/inbox"
import { GenericItem } from "../GenericItem"
import { getInboxItemRenderer, inboxFilters, registerInboxItemRenderer } from "../itemRenderers"
import { PortalMessageItem } from "../PortalMessageItem"
import { RefillItem } from "../RefillItem"

const ITEM: InboxItem = {
  kind: "voice",
  source_id: "v1",
  patient_id: null,
  patient_name: null,
  title: "Voicemail",
  detail: "Call me back",
  occurred_at: "2026-09-28T10:00:00Z",
  severity: "normal",
  href: "/somewhere",
  context: {},
  disposition: null,
  resolved_at: null,
  snoozed_until: null,
}

describe("inbox item renderers", () => {
  it("has a renderer for each built-in kind", () => {
    expect(getInboxItemRenderer("portal_message")).toBe(PortalMessageItem)
    expect(getInboxItemRenderer("refill")).toBe(RefillItem)
  })

  it("shows an unknown kind through the generic card, with a way to it", () => {
    const Renderer = getInboxItemRenderer("unheard_of")
    expect(Renderer).toBe(GenericItem)

    render(<Renderer item={ITEM} onClose={() => undefined} />)

    expect(screen.getByText("Call me back")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/somewhere")
  })

  it("lets other code register a kind", () => {
    function VoiceItem() {
      return <p>voice card</p>
    }
    registerInboxItemRenderer("voice", VoiceItem)

    const Renderer = getInboxItemRenderer("voice")
    render(<Renderer item={ITEM} onClose={() => undefined} />)

    expect(screen.getByText("voice card")).toBeInTheDocument()
  })

  it("offers a filter per built-in kind", () => {
    expect(inboxFilters.map((filter) => [filter.label, filter.kinds])).toEqual([
      ["All", []],
      ["Messages", ["portal_message"]],
      ["Refills", ["refill"]],
      ["Intake", ["intake_review"]],
      ["Notes", ["note_to_sign"]],
      ["Calendar", ["calendar_change"]],
    ])
  })
})
