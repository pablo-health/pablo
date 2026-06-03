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

/** The count-threshold evaluator strategy (mirrors backend EVALUATOR_CRITERIA). */
export const EVALUATOR_TYPE_CRITERIA = "criteria"

export interface DiagnosticOutcome {
  /**
   * Whether the rubric is met. `null` when the definition's evaluator makes no
   * algorithmic determination (e.g. `checklist`) — the clinician decides.
   */
  meetsCriteria: boolean | null
  /** Human-readable reasons the criteria are not met (empty when met). */
  unmetReasons: string[]
  /**
   * The definition's suggested code. For `criteria` it is emitted only when the
   * criteria are met; for `checklist` it is null (no code is suggested — the
   * clinician selects the specifier from the options). When set, only a suggestion.
   */
  suggestedIcd10: string | null
}

export function evaluateDefinition(
  definition: DiagnosticDefinition,
  criterionResponses: Record<string, boolean>,
  gateResponses: Record<string, boolean>,
): DiagnosticOutcome {
  // Only the count-threshold strategy produces a pass/fail verdict. Any other
  // strategy (checklist, or a type this client doesn't know) records no
  // determination — meetsCriteria is null — and suggests no code: the checklist
  // responses don't determine the ICD-10-CM specifier, so the clinician picks it.
  if (definition.evaluator_type !== EVALUATOR_TYPE_CRITERIA) {
    return {
      meetsCriteria: null,
      unmetReasons: [],
      suggestedIcd10: null,
    }
  }

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
