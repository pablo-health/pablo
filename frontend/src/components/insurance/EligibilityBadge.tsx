// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EligibilityBadge
 *
 * One line on what the last eligibility check found, for the patient header
 * and the coverage card. The copy rule is load-bearing: a 271 is what the
 * payer knew when it was asked, not a promise to pay, so the badge says
 * "Plan active as of <date>" and never "covered".
 */

import { AlertTriangle, CircleCheck, CircleHelp, CircleX } from "lucide-react"
import type { EligibilitySummary } from "@/types/coverage"

type BadgeTone = "good" | "bad" | "neutral" | "warn"

const TONE_STYLES: Record<BadgeTone, string> = {
  good: "bg-secondary-100 text-secondary-700",
  bad: "bg-red-100 text-red-700",
  neutral: "bg-neutral-100 text-neutral-700",
  warn: "bg-yellow-100 text-yellow-700",
}

const TONE_ICONS: Record<BadgeTone, typeof CircleCheck> = {
  good: CircleCheck,
  bad: CircleX,
  neutral: CircleHelp,
  warn: AlertTriangle,
}

function checkedOn(summary: EligibilitySummary): string {
  return new Date(summary.checked_at).toLocaleDateString()
}

/** The badge's text for a summary. Exported so the copy rule can be tested. */
export function eligibilityBadgeText(summary: EligibilitySummary | null): string {
  if (!summary) return "Plan not yet checked"
  switch (summary.status) {
    case "active":
      return `Plan active as of ${checkedOn(summary)}`
    case "inactive":
      return `Plan inactive as of ${checkedOn(summary)}`
    case "error":
      return `Payer could not confirm the plan (${checkedOn(summary)})`
    default:
      return `Plan status unknown as of ${checkedOn(summary)}`
  }
}

function toneFor(summary: EligibilitySummary | null): BadgeTone {
  if (!summary) return "neutral"
  switch (summary.status) {
    case "active":
      return summary.carveout_administrator ? "warn" : "good"
    case "inactive":
      return "bad"
    case "error":
      return "warn"
    default:
      return "neutral"
  }
}

interface EligibilityBadgeProps {
  summary: EligibilitySummary | null
  className?: string
}

export function EligibilityBadge({ summary, className = "" }: EligibilityBadgeProps) {
  const tone = toneFor(summary)
  const Icon = TONE_ICONS[tone]
  return (
    <span
      data-testid="eligibility-badge"
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${TONE_STYLES[tone]} ${className}`}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {eligibilityBadgeText(summary)}
    </span>
  )
}

/** The carve-out line: where behavioral claims actually go. */
export function carveoutText(summary: EligibilitySummary | null): string | null {
  const administrator = summary?.carveout_administrator
  if (!administrator) return null
  const id = administrator.payer_id ? ` (payer ID ${administrator.payer_id})` : ""
  return `Behavioral benefits administered by ${administrator.name}${id}. File claims there.`
}
