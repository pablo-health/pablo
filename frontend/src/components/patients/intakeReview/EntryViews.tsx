// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The questions answered by typing, the patient's own identity check, and
 * the two items that only carry text.
 *
 * What was typed is shown as text, under the question it answered. The
 * reason prompt and the identity heading are the engine's wording, read from
 * the form the server sent with the review, so the chart shows the question
 * the patient was actually asked.
 */

import { confirmedIn, correctionsIn } from "@/components/portal/forms/renderers/DemographicsItem"
import { unitOf, valueIn as numberValueIn } from "@/components/portal/forms/renderers/NumberItem"
import {
  CONTACT_NAME_LABEL,
  CONTACT_PHONE_LABEL,
  CONTACT_RELATIONSHIP_LABEL,
  IDENTITY_CONFIRM,
  IDENTITY_CORRECTIONS_LABEL,
  IDENTITY_DENY,
  IDENTITY_DOB_LABEL,
  IDENTITY_HEADING,
  IDENTITY_NAME_LABEL,
} from "@/components/portal/forms/formsCopy"
import type { ItemView, ItemViewProps } from "./types"
import { NoAnswer, OptionRow, ViewFrame, Written, questionOf } from "./ViewParts"

function textOf(value: Record<string, unknown> | null, key: string): string {
  const held = value?.[key]
  return typeof held === "string" ? held.trim() : ""
}

function TextAnswer({ text }: { text: string }) {
  return text === "" ? <NoAnswer /> : <Written text={text} />
}

function FreeTextView({ item }: ItemViewProps) {
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <TextAnswer text={textOf(item.value, "text")} />
    </ViewFrame>
  )
}

function ReasonView({ item, form }: ItemViewProps) {
  return (
    <ViewFrame heading={form.reason_prompt}>
      <TextAnswer text={textOf(item.value, "text")} />
    </ViewFrame>
  )
}

function NumberView({ item }: ItemViewProps) {
  const value = numberValueIn(item.value)
  const unit = unitOf(item.config)
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <TextAnswer text={value === null ? "" : unit === null ? String(value) : `${value} ${unit}`} />
    </ViewFrame>
  )
}

/** `YYYY-MM-DD` in the reader's own format, without a timezone shifting the day. */
function localDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number)
  if (!year || !month || !day) return iso
  return new Date(year, month - 1, day).toLocaleDateString()
}

function DateView({ item }: ItemViewProps) {
  const value = textOf(item.value, "value")
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <TextAnswer text={value === "" ? "" : localDate(value)} />
    </ViewFrame>
  )
}

const CONTACT_FIELDS = [
  { key: "name", label: CONTACT_NAME_LABEL },
  { key: "relationship", label: CONTACT_RELATIONSHIP_LABEL },
  { key: "phone", label: CONTACT_PHONE_LABEL },
] as const

function EmergencyContactView({ item }: ItemViewProps) {
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      {item.value === null ? (
        <NoAnswer />
      ) : (
        <dl className="space-y-2">
          {CONTACT_FIELDS.map((field) => (
            <div key={field.key}>
              <dt className="text-xs text-neutral-500">{field.label}</dt>
              <dd className="text-sm text-neutral-900">{textOf(item.value, field.key) || "—"}</dd>
            </div>
          ))}
        </dl>
      )}
    </ViewFrame>
  )
}

/**
 * The identity check, with the name and date of birth that were on it.
 *
 * Those come from the chart as it stands now, which is what the patient saw
 * unless somebody has since corrected the record — and a correction is
 * exactly what the patient's answer here may have asked for.
 */
function DemographicsView({ item, form }: ItemViewProps) {
  const confirmed = confirmedIn(item.value)
  const corrections = correctionsIn(item.value).trim()
  const { identity } = form
  return (
    <ViewFrame heading={IDENTITY_HEADING}>
      <dl className="mb-2 space-y-1 text-sm">
        <div className="flex justify-between gap-4">
          <dt className="text-neutral-500">{IDENTITY_NAME_LABEL}</dt>
          <dd className="text-right text-neutral-900">{identity.first_name} {identity.last_name}</dd>
        </div>
        {identity.date_of_birth && (
          <div className="flex justify-between gap-4">
            <dt className="text-neutral-500">{IDENTITY_DOB_LABEL}</dt>
            <dd className="text-right text-neutral-900">{identity.date_of_birth}</dd>
          </div>
        )}
      </dl>
      <div role="radiogroup" aria-label={IDENTITY_HEADING} className="flex flex-col gap-1.5">
        <OptionRow kind="radio" name={`view-${item.id}`} label={IDENTITY_CONFIRM} chosen={confirmed === true} />
        <OptionRow kind="radio" name={`view-${item.id}`} label={IDENTITY_DENY} chosen={confirmed === false} />
      </div>
      {confirmed === null && <NoAnswer />}
      {confirmed === false && corrections !== "" && (
        <div className="mt-2 space-y-1">
          <p className="text-sm text-neutral-700">{IDENTITY_CORRECTIONS_LABEL}</p>
          <Written text={corrections} />
        </div>
      )}
    </ViewFrame>
  )
}

function SectionView({ item }: ItemViewProps) {
  const title = typeof item.config.title === "string" ? item.config.title : questionOf(item)
  return <h3 className="text-base font-semibold text-neutral-900">{title}</h3>
}

function InstructionsView({ item }: ItemViewProps) {
  const body = typeof item.config.body_markdown === "string" ? item.config.body_markdown : ""
  return <p className="whitespace-pre-line text-sm text-neutral-600">{body}</p>
}

/**
 * A type this chart has no view for, spelled out rather than hidden.
 *
 * Answers arrive as an open mapping, so a `text` field is used when there is
 * one and the mapping is written out otherwise.
 */
function FallbackView({ item }: ItemViewProps) {
  const value = item.value
  const text =
    value === null
      ? ""
      : typeof value.text === "string"
        ? value.text
        : Object.entries(value)
            .filter(([, v]) => v !== null && v !== undefined && v !== "")
            .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
            .join(" · ")
  return (
    <ViewFrame heading={questionOf(item)} helpText={item.help_text}>
      <TextAnswer text={text.trim()} />
    </ViewFrame>
  )
}

export const freeTextView: ItemView = { Component: FreeTextView, answerable: true }
export const reasonView: ItemView = { Component: ReasonView, answerable: true }
export const numberView: ItemView = { Component: NumberView, answerable: true }
export const dateView: ItemView = { Component: DateView, answerable: true }
export const emergencyContactView: ItemView = { Component: EmergencyContactView, answerable: true }
export const demographicsView: ItemView = { Component: DemographicsView, answerable: true }
export const sectionView: ItemView = { Component: SectionView, answerable: false }
export const instructionsView: ItemView = { Component: InstructionsView, answerable: false }
export const fallbackView: ItemView = { Component: FallbackView, answerable: true }
