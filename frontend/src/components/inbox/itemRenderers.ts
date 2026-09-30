// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { createElement, type ComponentType, type ReactElement } from "react"
import type { InboxItem } from "@/lib/api/inbox"
import { CalendarChangeItem } from "./CalendarChangeItem"
import { GenericItem } from "./GenericItem"
import { IntakeReviewItem } from "./IntakeReviewItem"
import { inboxExtensions } from "./itemRenderers.extensions"
import { NoteToSignItem } from "./NoteToSignItem"
import { PortalMessageItem } from "./PortalMessageItem"
import { RefillItem } from "./RefillItem"

/**
 * How each kind of Inbox item is shown — the extension point for the Inbox.
 *
 * The server decides which kinds exist (each is a registered source); this
 * decides how an item of each kind reads once opened. A kind with no renderer
 * still shows, through `GenericItem`: its title, detail and a link to where
 * it is handled. Code outside this file adds a kind with
 * `registerInboxItemRenderer`, and a downstream build does it through the
 * `itemRenderers.extensions.ts` merge slot.
 */
export interface InboxItemRendererProps {
  item: InboxItem
  /** Close the item, back to the list on a narrow screen. */
  onClose: () => void
}

export type InboxItemRenderer = ComponentType<InboxItemRendererProps>

/** One filter chip above the list: a label and the kinds it shows. */
export interface InboxFilter {
  id: string
  label: string
  kinds: string[]
}

/**
 * Shape of the merge slot. Declared here in the stable file so a replacement
 * slot can import the type (it cannot import it from the file it replaces).
 */
export interface InboxExtensions {
  renderers: Record<string, InboxItemRenderer>
  filters: InboxFilter[]
}

const renderers = new Map<string, InboxItemRenderer>()

export function registerInboxItemRenderer(kind: string, renderer: InboxItemRenderer): void {
  renderers.set(kind, renderer)
}

export function getInboxItemRenderer(kind: string): InboxItemRenderer {
  return renderers.get(kind) ?? GenericItem
}

/** An item, through its kind's renderer. */
export function renderInboxItem(props: InboxItemRendererProps): ReactElement {
  return createElement(getInboxItemRenderer(props.item.kind), props)
}

registerInboxItemRenderer("portal_message", PortalMessageItem)
registerInboxItemRenderer("refill", RefillItem)
registerInboxItemRenderer("intake_review", IntakeReviewItem)
registerInboxItemRenderer("note_to_sign", NoteToSignItem)
registerInboxItemRenderer("calendar_change", CalendarChangeItem)
for (const [kind, renderer] of Object.entries(inboxExtensions.renderers)) {
  registerInboxItemRenderer(kind, renderer)
}

/** "All" first, then one filter per built-in kind, then whatever the slot adds. */
export const inboxFilters: InboxFilter[] = [
  { id: "all", label: "All", kinds: [] },
  { id: "messages", label: "Messages", kinds: ["portal_message"] },
  { id: "refills", label: "Refills", kinds: ["refill"] },
  { id: "intake", label: "Intake", kinds: ["intake_review"] },
  { id: "notes", label: "Notes", kinds: ["note_to_sign"] },
  { id: "calendar", label: "Calendar", kinds: ["calendar_change"] },
  ...inboxExtensions.filters,
]
