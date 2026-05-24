// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Static UI metadata for each ``SourceKey``. The chip rail, the
 * manifest disclosure, and the "add source" menu all read from here so
 * the visual treatment + copy stay consistent.
 *
 * Labels are intentionally clinician-friendly and noun-form
 * ("Progress notes", not "Recent session notes from the past two
 * weeks"); detail belongs in the popover, not the chip.
 */

import {
  ClipboardList,
  FileText,
  Files,
  HeartPulse,
  Notebook,
  Pill,
  ShieldAlert,
  Stethoscope,
  TestTube,
  Type,
  type LucideIcon,
} from "lucide-react"

import type { SourceFamily, SourceKey } from "./types"

interface SourceMeta {
  /** Display label for the chip + manifest. */
  label: string
  /** Slightly longer description for the popover header / add-source menu. */
  description: string
  /** Family drives the chip's left-edge color band per §13.2. */
  family: SourceFamily
  icon: LucideIcon
}

export const SOURCE_META: Record<SourceKey, SourceMeta> = {
  pasted_text: {
    label: "Pasted text",
    description: "Free-text you paste in for this conversation only.",
    family: "manual",
    icon: Type,
  },
  current_medications: {
    label: "Medications",
    description: "Current medication list from the patient's chart.",
    family: "documents",
    icon: Pill,
  },
  most_recent_intake: {
    label: "Intake",
    description: "Most recent intake / biopsychosocial note.",
    family: "documents",
    icon: ClipboardList,
  },
  progress_notes_recent: {
    label: "Progress notes",
    description: "Most recent session notes.",
    family: "sessions",
    icon: Notebook,
  },
  progress_notes_explicit: {
    label: "Selected sessions",
    description: "Specific session notes you've pinned to this conversation.",
    family: "sessions",
    icon: FileText,
  },
  patient_documents: {
    label: "Uploaded documents",
    description:
      "PDFs you've uploaded to this patient's chart (prior-provider records, intake packets, labs).",
    family: "documents",
    icon: Files,
  },
  treatment_plan_active: {
    label: "Treatment plan",
    description: "Active treatment plan.",
    family: "documents",
    icon: Stethoscope,
  },
  safety_plan_active: {
    label: "Safety plan",
    description: "Active safety plan.",
    family: "documents",
    icon: ShieldAlert,
  },
  lab_values_recent: {
    label: "Labs",
    description: "Recent lab values.",
    family: "documents",
    icon: TestTube,
  },
  vitals_recent: {
    label: "Vitals",
    description: "Recent vitals.",
    family: "documents",
    icon: HeartPulse,
  },
}

/**
 * Tailwind class fragments for each family. The chip composes its own
 * className from ``cn()`` so these are intentionally bare snippets, not
 * full class strings.
 */
export const FAMILY_STYLES: Record<
  SourceFamily,
  { border: string; activeBg: string; activeText: string; inactiveText: string }
> = {
  sessions: {
    border: "border-l-secondary-500",
    activeBg: "bg-secondary-100",
    activeText: "text-secondary-900",
    inactiveText: "text-secondary-700",
  },
  documents: {
    border: "border-l-primary-500",
    activeBg: "bg-primary-100",
    activeText: "text-neutral-900",
    inactiveText: "text-neutral-700",
  },
  manual: {
    border: "border-l-neutral-500",
    activeBg: "bg-neutral-100",
    activeText: "text-neutral-900",
    inactiveText: "text-neutral-600",
  },
}
