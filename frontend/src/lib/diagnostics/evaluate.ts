// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Client-side mirror of the backend "criteria" evaluator
 * (`app.diagnostics.evaluator._evaluate_criteria`).
 *
 * Used only to render a *live* determination preview while the clinician fills
 * the form — the server recomputes authoritatively on save and returns the
 * canonical `meets_criteria` / `unmet_reasons`. The reason strings here are
 * kept byte-for-byte identical to the backend so the preview and the saved
 * record never disagree. A criterion counts only when explicitly `true`;
 * missing or `false` does not. Every gate must be explicitly `true`.
 */

import type { DiagnosticDefinition } from "@/types/diagnoses"

export interface DiagnosticOutcome {
  meetsCriteria: boolean
  /** Human-readable reasons the criteria are not met (empty when met). */
  unmetReasons: string[]
  /** The definition's suggested code when criteria are met, else null. */
  suggestedIcd10: string | null
}

export function evaluateDefinition(
  definition: DiagnosticDefinition,
  criterionResponses: Record<string, boolean>,
  gateResponses: Record<string, boolean>,
): DiagnosticOutcome {
  const reasons: string[] = []

  for (const group of definition.criterion_groups) {
    const met = group.criteria.filter(
      (c) => criterionResponses[c.key] === true,
    )
    if (met.length < group.min_met) {
      reasons.push(
        `${group.label}: needs at least ${group.min_met}, ${met.length} met`,
      )
    }
    if (group.require_cardinal && !met.some((c) => c.cardinal)) {
      reasons.push(`${group.label}: requires at least one core symptom`)
    }
  }

  for (const gate of definition.gates) {
    if (gateResponses[gate.key] !== true) {
      reasons.push(`Not met: ${gate.label}`)
    }
  }

  const meetsCriteria = reasons.length === 0
  return {
    meetsCriteria,
    unmetReasons: reasons,
    suggestedIcd10: meetsCriteria ? definition.suggested_icd10 : null,
  }
}
