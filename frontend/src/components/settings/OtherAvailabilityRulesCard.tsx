// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Every availability rule the other Your-hours cards do not show: rules for
 * one appointment type ("two intakes a week", "intakes on Tuesday
 * afternoons"), and the practice-wide weekly cap and before-session buffer,
 * which have no row of their own. Without this a rule saved from a sentence
 * could exist, shape every offer, and be impossible to find or remove.
 */

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useAvailabilityRules, useDeleteAvailabilityRule } from "@/hooks/useAvailability"
import { useAppointmentTypes } from "@/hooks/useAppointmentTypes"
import { isPracticeWide, type AvailabilityRule, type RuleType } from "@/types/availability"
import { RULE_TYPE_LABELS, summarize } from "./AvailabilitySettings"

/** Practice-wide rule types another card already lists and edits. */
const SHOWN_ELSEWHERE: RuleType[] = [
  "working_hours",
  "max_per_day",
  "buffer_after",
  "session_defaults",
  "block_day_of_week",
  "block_time_range",
  "block_date_range",
  "block_specific_dates",
]

export function otherRules(rules: AvailabilityRule[]): AvailabilityRule[] {
  return rules.filter((rule) => !isPracticeWide(rule) || !SHOWN_ELSEWHERE.includes(rule.rule_type))
}

export function scopeLabel(rule: AvailabilityRule, typeName: string | null): string | null {
  if (isPracticeWide(rule)) return null
  const name = typeName ?? "One appointment type"
  return rule.allow_other_types === false ? `Only ${name} in this time` : `${name} only`
}

export function OtherAvailabilityRulesCard() {
  const { data } = useAvailabilityRules()
  const { data: types } = useAppointmentTypes()
  const deleteMutation = useDeleteAvailabilityRule()
  const [error, setError] = useState<string | null>(null)

  const rules = otherRules(data?.data ?? [])
  const typeNames = new Map((types?.data ?? []).map((t) => [t.id, t.name]))

  if (rules.length === 0) return null

  function remove(rule: AvailabilityRule) {
    setError(null)
    deleteMutation.mutate(rule.id, {
      onError: () => setError("That rule could not be removed. Try again."),
    })
  }

  return (
    <div className="px-[22px] py-3.5" data-testid="other-availability-rules">
      <ul className="divide-y divide-border">
        {rules.map((rule) => {
          const scope = scopeLabel(
            rule,
            rule.appointment_type_id ? (typeNames.get(rule.appointment_type_id) ?? null) : null,
          )
          return (
            <li key={rule.id} className="flex items-center justify-between gap-4 py-3 first:pt-0">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  {RULE_TYPE_LABELS[rule.rule_type]}
                </p>
                <p className="text-[12.5px] text-muted-foreground">
                  {summarize(rule)}
                  {scope ? ` · ${scope}` : ""}
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="text-red-600"
                onClick={() => remove(rule)}
                disabled={deleteMutation.isPending}
              >
                Remove
              </Button>
            </li>
          )
        })}
      </ul>
      {error && (
        <p role="alert" className="pt-2 text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  )
}
