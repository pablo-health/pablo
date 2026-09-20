// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The intake form builder's wire shapes.
 *
 * `config` stays an open record rather than a per-type union. The server owns
 * what a valid configuration is and says so at publish; a second copy of those
 * rules here would be a second thing to keep in step, and the editor's job is
 * to collect the fields, not to adjudicate them. The per-type forms in
 * `components/settings/intake/` narrow it where they read it.
 */

/** Every kind of item a form can hold, in the order the editor offers them. */
export const ITEM_TYPES = [
  "section",
  "instructions",
  "demographics",
  "reason",
  "free_text",
  "single_choice",
  "multi_choice",
  "yes_no",
  "scale",
  "number",
  "date",
  "instrument",
  "emergency_contact",
  "guardian",
  "consent_document",
  "insurance_card",
  "document_request",
] as const

export type ItemType = (typeof ITEM_TYPES)[number]

/** Items that show text and collect nothing. Never required. */
export const DISPLAY_ONLY_ITEM_TYPES: readonly ItemType[] = ["section", "instructions"]

export type ItemConfig = Record<string, unknown>

export interface ChoiceOption {
  key: string
  label: string
}

export interface IntakeItem {
  id: string
  key: string
  position: number
  item_type: ItemType
  required: boolean
  resign_on_new_version: boolean
  config: ItemConfig
}

export interface IntakeItemInput {
  key: string
  item_type: ItemType
  required: boolean
  resign_on_new_version: boolean
  config: ItemConfig
}

export interface IntakeVersion {
  id: string
  version: number
  /** Set means frozen. That is the whole of a version's state. */
  published_at: string | null
  created_at: string
}

export interface IntakeVersionDetail extends IntakeVersion {
  template_id: string
  items: IntakeItem[]
}

export interface IntakeTemplate {
  id: string
  name: string
  created_at: string
  archived_at: string | null
  versions: IntakeVersion[]
}
