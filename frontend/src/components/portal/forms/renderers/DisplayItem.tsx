// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The two items that show text and collect nothing: a section heading and a
 * block of instructions.
 *
 * Never saved and never required — the save route refuses one, and the
 * server's completion walk passes over them. So the walk shows them, the
 * patient reads them and presses Continue, and nothing is sent.
 *
 * The instructions body is stored as markdown and rendered as plain text
 * with its line breaks kept. Nothing on this surface interprets markup a
 * practice typed into a settings field, and a paragraph break is the only
 * part of it a patient would miss.
 */

import { ITEM_UNAVAILABLE } from "../formsCopy"
import type { ItemRenderer, ItemRendererProps } from "./types"

function SectionItem({ item }: ItemRendererProps) {
  const title = typeof item.config.title === "string" ? item.config.title : null
  if (title === null) return <Unavailable />
  return (
    <section data-testid="forms-item-section">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
    </section>
  )
}

function InstructionsItem({ item }: ItemRendererProps) {
  const body = typeof item.config.body_markdown === "string" ? item.config.body_markdown : null
  if (body === null) return <Unavailable />
  return (
    <section data-testid="forms-item-instructions">
      <p className="whitespace-pre-line text-sm leading-relaxed text-neutral-700">{body}</p>
    </section>
  )
}

/**
 * A question this version of the portal cannot ask.
 *
 * Two cases reach it and they read the same to a patient: an item type whose
 * screens have not shipped, and a stored item missing the wording its type
 * needs. In both the honest thing to say is that this step is not ready, and
 * the server agrees — it refuses an answer to either, so the form cannot be
 * handed in while one is still required.
 */
export function Unavailable() {
  return (
    <section data-testid="forms-item-unavailable" className="py-4">
      <p className="text-sm text-neutral-600">{ITEM_UNAVAILABLE}</p>
    </section>
  )
}

export const sectionRenderer: ItemRenderer = {
  Component: SectionItem,
  answerable: false,
  label: () => "",
  summary: () => null,
}

export const instructionsRenderer: ItemRenderer = {
  Component: InstructionsItem,
  answerable: false,
  label: () => "",
  summary: () => null,
}

export const unavailableRenderer: ItemRenderer = {
  Component: Unavailable,
  answerable: false,
  label: () => "",
  summary: () => null,
}
