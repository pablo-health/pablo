// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AvailabilitySettings
 *
 * Lists a therapist's availability rules — working hours, blocked days or
 * date ranges, per-day appointment caps, and appointment buffers — grouped
 * by type, with create/edit/delete. Evaluation of these rules against
 * proposed bookings happens server-side; this surface only manages them.
 */

"use client"

import { useState } from "react"
import { CalendarClock, PencilLine, Plus, Trash2 } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import {
  useAvailabilityRules,
  useDeleteAvailabilityRule,
} from "@/hooks/useAvailabilityRules"
import { AvailabilityRuleModal } from "./AvailabilityRuleModal"
import {
  DAY_OF_WEEK_OPTIONS,
  RULE_TYPE_OPTIONS,
  type AvailabilityRuleResponse,
  type RuleType,
} from "@/types/availability"

function dayLabel(value: unknown): string {
  const day = DAY_OF_WEEK_OPTIONS.find((d) => d.value === Number(value))
  return day?.label ?? String(value)
}

function summarizeRule(rule: AvailabilityRuleResponse): string {
  const p = rule.params
  switch (rule.rule_type as RuleType) {
    case "working_hours":
      return `${dayLabel(p.day_of_week)}, ${p.start}–${p.end}`
    case "block_day_of_week":
      return dayLabel(p.day_of_week)
    case "block_time_range":
      return `${p.start}–${p.end}, every day`
    case "max_per_day":
      return `${p.max} appointment${Number(p.max) === 1 ? "" : "s"} per day`
    case "buffer_before":
      return `${p.minutes} minute${Number(p.minutes) === 1 ? "" : "s"} before every appointment`
    case "buffer_after":
      return `${p.minutes} minute${Number(p.minutes) === 1 ? "" : "s"} after every appointment`
    case "block_date_range":
      return `${p.start_date} to ${p.end_date}`
    case "block_specific_dates":
      return Array.isArray(p.dates) ? p.dates.join(", ") : ""
    default:
      return ""
  }
}

export function AvailabilitySettings() {
  const { data, isLoading, error } = useAvailabilityRules()
  const deleteRule = useDeleteAvailabilityRule()

  const [addOpen, setAddOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<AvailabilityRuleResponse | null>(null)

  async function handleDelete(rule: AvailabilityRuleResponse) {
    const label = RULE_TYPE_OPTIONS.find((r) => r.value === rule.rule_type)?.label ?? "rule"
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete this ${label.toLowerCase()} rule? This cannot be undone.`)
    ) {
      return
    }
    await deleteRule.mutateAsync({ ruleId: rule.id })
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load availability rules."}
      </p>
    )
  }

  const rules = data?.data ?? []
  const typeOrder = RULE_TYPE_OPTIONS.map((r) => r.value)
  const sorted = [...rules].sort(
    (a, b) =>
      typeOrder.indexOf(a.rule_type as RuleType) - typeOrder.indexOf(b.rule_type as RuleType),
  )

  if (sorted.length === 0) {
    return (
      <>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <CalendarClock className="h-8 w-8 text-neutral-300" />
          <p className="text-sm text-neutral-600 max-w-sm">
            No availability rules yet. A rule limits when appointments can be
            booked — for example, blocking a weekday, capping how many
            appointments happen per day, or requiring a buffer between
            sessions.
          </p>
          <Button size="sm" onClick={() => setAddOpen(true)} className="gap-1.5">
            <Plus className="h-4 w-4" />
            Add rule
          </Button>
        </div>
        <AvailabilityRuleModal open={addOpen} onOpenChange={setAddOpen} />
      </>
    )
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setAddOpen(true)} className="gap-1.5">
            <Plus className="h-4 w-4" />
            Add rule
          </Button>
        </div>

        <ul className="space-y-2">
          {sorted.map((rule) => {
            const typeMeta = RULE_TYPE_OPTIONS.find((r) => r.value === rule.rule_type)
            const isDeleting =
              deleteRule.isPending && deleteRule.variables?.ruleId === rule.id

            return (
              <li
                key={rule.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5"
              >
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-sm text-neutral-900 truncate">
                      {typeMeta?.label ?? rule.rule_type}
                    </span>
                    <span
                      className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                        rule.enforcement === "hard"
                          ? "bg-red-100 text-red-800"
                          : "bg-yellow-100 text-yellow-800"
                      }`}
                    >
                      {rule.enforcement === "hard" ? "Hard" : "Soft"}
                    </span>
                  </span>
                  <span className="text-xs text-neutral-500 truncate">
                    {summarizeRule(rule)}
                  </span>
                </span>

                <span className="flex shrink-0 items-center gap-2">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title="Edit"
                    onClick={() => setEditTarget(rule)}
                  >
                    <PencilLine className="h-3.5 w-3.5" />
                    <span className="sr-only">Edit {typeMeta?.label ?? rule.rule_type}</span>
                  </Button>

                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title="Delete"
                    disabled={isDeleting}
                    onClick={() => handleDelete(rule)}
                    className="text-red-500 hover:text-red-700 hover:bg-red-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    <span className="sr-only">Delete {typeMeta?.label ?? rule.rule_type}</span>
                  </Button>
                </span>
              </li>
            )
          })}
        </ul>
      </div>

      <AvailabilityRuleModal open={addOpen} onOpenChange={setAddOpen} />

      {editTarget && (
        <AvailabilityRuleModal
          open={!!editTarget}
          onOpenChange={(open) => {
            if (!open) setEditTarget(null)
          }}
          initialData={editTarget}
        />
      )}
    </>
  )
}
