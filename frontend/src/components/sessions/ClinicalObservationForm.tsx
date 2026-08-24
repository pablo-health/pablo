// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClinicalObservationForm Component
 *
 * Form for therapists to add clinical observations that the AI
 * cannot infer from a transcript (appearance, eye contact,
 * psychomotor activity, etc.). Supports edit and read-only modes.
 */

"use client"

import type { ClinicalObservation } from "@/types/sessions"
import { cn } from "@/lib/utils"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export interface ClinicalObservationFormProps {
  value: ClinicalObservation
  onChange: (observation: ClinicalObservation) => void
  readonly?: boolean
  className?: string
}

const APPEARANCE_OPTIONS = [
  "well-groomed",
  "disheveled",
  "appropriately dressed",
  "unkempt",
] as const

const EYE_CONTACT_OPTIONS = [
  "appropriate",
  "intermittent",
  "poor",
  "avoidant",
] as const

const PSYCHOMOTOR_OPTIONS = [
  "normal",
  "agitation",
  "retardation",
  "restless",
] as const

const ATTITUDE_OPTIONS = [
  "cooperative",
  "guarded",
  "hostile",
  "defensive",
  "withdrawn",
] as const

const AFFECT_CONGRUENCE_OPTIONS = ["congruent", "incongruent"] as const

const AFFECT_RANGE_OPTIONS = ["full", "restricted", "blunted", "flat"] as const

const AFFECT_STABILITY_OPTIONS = ["stable", "labile"] as const

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/**
 * Combine the structured affect fields into a single readable phrase,
 * e.g. "congruent, restricted range, stable" with optional trailing notes.
 */
function formatAffect(obs: {
  affect_congruence: string
  affect_range: string
  affect_stability: string
  affect_notes: string
}): string {
  const parts: string[] = []
  if (obs.affect_congruence) parts.push(obs.affect_congruence)
  if (obs.affect_range) parts.push(`${obs.affect_range} range`)
  if (obs.affect_stability) parts.push(obs.affect_stability)
  const structured = parts.join(", ")

  if (structured && obs.affect_notes) {
    return `${structured} — ${obs.affect_notes}`
  }
  return structured || obs.affect_notes
}

export function ClinicalObservationForm({
  value,
  onChange,
  readonly = false,
  className,
}: ClinicalObservationFormProps) {
  const { readOnly } = useReadOnlyMode()

  const update = (field: keyof ClinicalObservation, fieldValue: string) => {
    onChange({ ...value, [field]: fieldValue })
  }

  if (readonly || readOnly) {
    return (
      <div className={cn("rounded-lg border border-teal-200 bg-teal-50/50 p-4", className)}>
        <h3 className="text-sm font-semibold text-teal-800 mb-3">
          Clinician Observations
        </h3>
        <dl className="space-y-2 text-sm">
          <ReadonlyField label="Appearance" value={value.appearance} />
          <ReadonlyField label="Eye Contact" value={value.eye_contact} />
          <ReadonlyField
            label="Psychomotor Activity"
            value={
              value.psychomotor_notes
                ? `${capitalize(value.psychomotor)} — ${value.psychomotor_notes}`
                : value.psychomotor
                  ? capitalize(value.psychomotor)
                  : ""
            }
          />
          <ReadonlyField label="Attitude / Behavior" value={value.attitude ? capitalize(value.attitude) : ""} />
          <ReadonlyField label="Non-verbal" value={value.non_verbal} />
          <ReadonlyField label="Affect" value={formatAffect(value)} />
        </dl>
      </div>
    )
  }

  return (
    <fieldset
      className={cn("rounded-lg border border-teal-200 bg-teal-50/50 p-4", className)}
    >
      <legend className="text-sm font-semibold text-teal-800 px-1">
        Clinician Observations
      </legend>

      <div className="space-y-4 mt-2">
        {/* Appearance */}
        <div>
          <label
            htmlFor="obs-appearance"
            className="block text-sm font-medium text-neutral-600 mb-1"
          >
            Appearance
          </label>
          <div className="flex gap-2">
            <Select
              value={APPEARANCE_OPTIONS.includes(value.appearance as typeof APPEARANCE_OPTIONS[number]) ? value.appearance : value.appearance ? "custom" : undefined}
              onValueChange={(v) => update("appearance", v === "custom" ? "" : v)}
            >
              <SelectTrigger id="obs-appearance" className="w-full">
                <SelectValue placeholder="Select..." />
              </SelectTrigger>
              <SelectContent>
                {APPEARANCE_OPTIONS.map((opt) => (
                  <SelectItem key={opt} value={opt}>
                    {capitalize(opt)}
                  </SelectItem>
                ))}
                <SelectItem value="custom">Notable changes (free text)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {!APPEARANCE_OPTIONS.includes(value.appearance as typeof APPEARANCE_OPTIONS[number]) && (
            <input
              type="text"
              value={value.appearance}
              onChange={(e) => update("appearance", e.target.value)}
              placeholder="Describe notable changes..."
              className="mt-2 w-full p-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              aria-label="Appearance notes"
            />
          )}
        </div>

        {/* Eye Contact */}
        <div>
          <label
            htmlFor="obs-eye-contact"
            className="block text-sm font-medium text-neutral-600 mb-1"
          >
            Eye Contact
          </label>
          <Select
            value={EYE_CONTACT_OPTIONS.includes(value.eye_contact as typeof EYE_CONTACT_OPTIONS[number]) ? value.eye_contact : value.eye_contact ? "custom" : undefined}
            onValueChange={(v) => update("eye_contact", v === "custom" ? "" : v)}
          >
            <SelectTrigger id="obs-eye-contact" className="w-full">
              <SelectValue placeholder="Select..." />
            </SelectTrigger>
            <SelectContent>
              {EYE_CONTACT_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {capitalize(opt)}
                </SelectItem>
              ))}
              <SelectItem value="custom">Other (free text)</SelectItem>
            </SelectContent>
          </Select>
          {!EYE_CONTACT_OPTIONS.includes(value.eye_contact as typeof EYE_CONTACT_OPTIONS[number]) && (
            <input
              type="text"
              value={value.eye_contact}
              onChange={(e) => update("eye_contact", e.target.value)}
              placeholder="Describe eye contact..."
              className="mt-2 w-full p-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              aria-label="Eye contact notes"
            />
          )}
        </div>

        {/* Psychomotor Activity */}
        <div>
          <label
            htmlFor="obs-psychomotor"
            className="block text-sm font-medium text-neutral-600 mb-1"
          >
            Psychomotor Activity
          </label>
          <Select
            value={value.psychomotor || undefined}
            onValueChange={(v) => update("psychomotor", v)}
          >
            <SelectTrigger id="obs-psychomotor" className="w-full">
              <SelectValue placeholder="Select..." />
            </SelectTrigger>
            <SelectContent>
              {PSYCHOMOTOR_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {capitalize(opt)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <input
            type="text"
            value={value.psychomotor_notes}
            onChange={(e) => update("psychomotor_notes", e.target.value)}
            placeholder="Additional psychomotor notes..."
            className="mt-2 w-full p-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            aria-label="Psychomotor notes"
          />
        </div>

        {/* Attitude / Behavior */}
        <div>
          <label
            htmlFor="obs-attitude"
            className="block text-sm font-medium text-neutral-600 mb-1"
          >
            Attitude / Behavior
          </label>
          <Select
            value={ATTITUDE_OPTIONS.includes(value.attitude as typeof ATTITUDE_OPTIONS[number]) ? value.attitude : value.attitude ? "custom" : undefined}
            onValueChange={(v) => update("attitude", v === "custom" ? "" : v)}
          >
            <SelectTrigger id="obs-attitude" className="w-full">
              <SelectValue placeholder="Select..." />
            </SelectTrigger>
            <SelectContent>
              {ATTITUDE_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {capitalize(opt)}
                </SelectItem>
              ))}
              <SelectItem value="custom">Other (free text)</SelectItem>
            </SelectContent>
          </Select>
          {!ATTITUDE_OPTIONS.includes(value.attitude as typeof ATTITUDE_OPTIONS[number]) && (
            <input
              type="text"
              value={value.attitude}
              onChange={(e) => update("attitude", e.target.value)}
              placeholder="Describe attitude/behavior..."
              className="mt-2 w-full p-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              aria-label="Attitude notes"
            />
          )}
        </div>

        {/* Non-verbal */}
        <div>
          <label
            htmlFor="obs-non-verbal"
            className="block text-sm font-medium text-neutral-600 mb-1"
          >
            Non-verbal
          </label>
          <textarea
            id="obs-non-verbal"
            value={value.non_verbal}
            onChange={(e) => update("non_verbal", e.target.value)}
            placeholder="Body language, gestures, posture notes..."
            className="w-full min-h-[80px] p-3 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
        </div>

        {/* Affect */}
        <div>
          <span className="block text-sm font-medium text-neutral-600 mb-1">
            Affect
          </span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label htmlFor="obs-affect-congruence" className="sr-only">
                Affect Congruence
              </label>
              <Select
                value={value.affect_congruence || undefined}
                onValueChange={(v) => update("affect_congruence", v)}
              >
                <SelectTrigger id="obs-affect-congruence" className="w-full">
                  <SelectValue placeholder="Congruence..." />
                </SelectTrigger>
                <SelectContent>
                  {AFFECT_CONGRUENCE_OPTIONS.map((opt) => (
                    <SelectItem key={opt} value={opt}>
                      {capitalize(opt)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label htmlFor="obs-affect-range" className="sr-only">
                Affect Range
              </label>
              <Select
                value={value.affect_range || undefined}
                onValueChange={(v) => update("affect_range", v)}
              >
                <SelectTrigger id="obs-affect-range" className="w-full">
                  <SelectValue placeholder="Range..." />
                </SelectTrigger>
                <SelectContent>
                  {AFFECT_RANGE_OPTIONS.map((opt) => (
                    <SelectItem key={opt} value={opt}>
                      {capitalize(opt)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label htmlFor="obs-affect-stability" className="sr-only">
                Affect Stability
              </label>
              <Select
                value={value.affect_stability || undefined}
                onValueChange={(v) => update("affect_stability", v)}
              >
                <SelectTrigger id="obs-affect-stability" className="w-full">
                  <SelectValue placeholder="Stability..." />
                </SelectTrigger>
                <SelectContent>
                  {AFFECT_STABILITY_OPTIONS.map((opt) => (
                    <SelectItem key={opt} value={opt}>
                      {capitalize(opt)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <textarea
            id="obs-affect-notes"
            value={value.affect_notes}
            onChange={(e) => update("affect_notes", e.target.value)}
            placeholder="Additional affect notes..."
            className="mt-2 w-full min-h-[80px] p-3 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            aria-label="Affect notes"
          />
        </div>
      </div>
    </fieldset>
  )
}

function ReadonlyField({ label, value }: { label: string; value: string }) {
  if (!value) return null
  return (
    <div>
      <dt className="font-medium text-neutral-500">{label}</dt>
      <dd className="text-neutral-800 mt-0.5">{value}</dd>
    </div>
  )
}

/**
 * Format a ClinicalObservation into a readable text block
 * suitable for merging into the Objective narrative.
 */
export function formatClinicalObservation(obs: ClinicalObservation): string {
  const lines: string[] = ["**Clinician Observations:**"]

  if (obs.appearance) {
    lines.push(`\n**Appearance:** ${capitalize(obs.appearance)}`)
  }
  if (obs.eye_contact) {
    lines.push(`**Eye Contact:** ${capitalize(obs.eye_contact)}`)
  }
  if (obs.psychomotor) {
    const psychomotorText = obs.psychomotor_notes
      ? `${capitalize(obs.psychomotor)} — ${obs.psychomotor_notes}`
      : capitalize(obs.psychomotor)
    lines.push(`**Psychomotor Activity:** ${psychomotorText}`)
  }
  if (obs.attitude) {
    lines.push(`**Attitude:** ${capitalize(obs.attitude)}`)
  }
  if (obs.non_verbal) {
    lines.push(`**Non-verbal:** ${obs.non_verbal}`)
  }
  const affectText = formatAffect(obs)
  if (affectText) {
    lines.push(`**Affect:** ${affectText}`)
  }

  return lines.join("\n")
}

/**
 * Default empty ClinicalObservation for initializing state.
 */
export const EMPTY_CLINICAL_OBSERVATION: ClinicalObservation = {
  appearance: "",
  eye_contact: "",
  psychomotor: "",
  psychomotor_notes: "",
  attitude: "",
  non_verbal: "",
  affect_congruence: "",
  affect_range: "",
  affect_stability: "",
  affect_notes: "",
}
