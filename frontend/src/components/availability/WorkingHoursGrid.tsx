// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * The one weekday-hours picker in the app: a weekday checkbox grid plus a
 * single start/end range applied to every checked day. The onboarding
 * schedule step and the calendar's first-run hours step both render this,
 * so there is never a second picker to keep in sync.
 *
 * It owns no saving. A host decides what a selection means and when it is
 * written; `workingHoursRules` turns a selection into the create-rule
 * payloads both hosts send.
 */

import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { AvailabilityRule, CreateAvailabilityRuleRequest } from "@/types/availability"

export const WEEKDAYS = [
  { day_of_week: 0, label: "Monday" },
  { day_of_week: 1, label: "Tuesday" },
  { day_of_week: 2, label: "Wednesday" },
  { day_of_week: 3, label: "Thursday" },
  { day_of_week: 4, label: "Friday" },
  { day_of_week: 5, label: "Saturday" },
  { day_of_week: 6, label: "Sunday" },
] as const

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => i)

export interface WorkingHoursSelection {
  days: ReadonlySet<number>
  startHour: number
  endHour: number
}

export const DEFAULT_WORKING_HOURS: WorkingHoursSelection = {
  days: new Set([0, 1, 2, 3, 4]),
  startHour: 9,
  endHour: 17,
}

export function formatHour(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`
}

function dayLabel(day: number): string {
  return WEEKDAYS.find((d) => d.day_of_week === day)?.label ?? ""
}

export function isCompleteSelection(selection: WorkingHoursSelection): boolean {
  return selection.days.size > 0 && selection.endHour > selection.startHour
}

/** One `working_hours` rule per checked weekday, all sharing the range. */
export function workingHoursRules(
  selection: WorkingHoursSelection
): CreateAvailabilityRuleRequest[] {
  const start = formatHour(selection.startHour)
  const end = formatHour(selection.endHour)
  return WEEKDAYS.filter(({ day_of_week }) => selection.days.has(day_of_week)).map(
    ({ day_of_week }) => ({
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week, start, end },
    })
  )
}

/** "Monday to Thursday, 09:00 to 17:00" — consecutive days collapse. */
export function describeWorkingHours(selection: WorkingHoursSelection): string {
  const days = [...selection.days].sort((a, b) => a - b)
  if (days.length === 0) return "No days selected"

  const runs: string[] = []
  let runStart = days[0]
  let previous = days[0]
  for (const day of days.slice(1)) {
    if (day === previous + 1) {
      previous = day
      continue
    }
    runs.push(describeRun(runStart, previous))
    runStart = day
    previous = day
  }
  runs.push(describeRun(runStart, previous))

  const hours = `${formatHour(selection.startHour)} to ${formatHour(selection.endHour)}`
  return `${runs.join(", ")}, ${hours}`
}

function describeRun(first: number, last: number): string {
  if (first === last) return dayLabel(first)
  if (last === first + 1) return `${dayLabel(first)} and ${dayLabel(last)}`
  return `${dayLabel(first)} to ${dayLabel(last)}`
}

/**
 * The selection a set of `working_hours` rules describes, as the grid
 * would have to be set to produce them. Days keep their own start/end in
 * the rules, so the range shown is the widest one across them — enough to
 * pre-fill the grid for a correction, never a silent write.
 */
export function selectionFromRules(
  rules: readonly Pick<AvailabilityRule, "rule_type" | "params">[]
): WorkingHoursSelection | null {
  const days = new Set<number>()
  let startHour: number | null = null
  let endHour: number | null = null
  for (const rule of rules) {
    if (rule.rule_type !== "working_hours") continue
    const day = rule.params.day_of_week
    if (typeof day !== "number") continue
    days.add(day)
    const start = hourOf(rule.params.start)
    const end = hourOf(rule.params.end)
    if (start !== null && (startHour === null || start < startHour)) startHour = start
    if (end !== null && (endHour === null || end > endHour)) endHour = end
  }
  if (days.size === 0 || startHour === null || endHour === null) return null
  return { days, startHour, endHour }
}

function hourOf(value: unknown): number | null {
  if (typeof value !== "string") return null
  const hour = Number(value.split(":")[0])
  return Number.isInteger(hour) && hour >= 0 && hour <= 23 ? hour : null
}

interface WorkingHoursGridProps {
  value: WorkingHoursSelection
  onChange: (next: WorkingHoursSelection) => void
  disabled?: boolean
}

export function WorkingHoursGrid({ value, onChange, disabled }: WorkingHoursGridProps) {
  function toggleDay(day: number) {
    const days = new Set(value.days)
    if (days.has(day)) days.delete(day)
    else days.add(day)
    onChange({ ...value, days })
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {WEEKDAYS.map(({ day_of_week, label }) => (
          <div key={day_of_week} className="flex items-center gap-2">
            <Checkbox
              id={`working-hours-day-${day_of_week}`}
              checked={value.days.has(day_of_week)}
              onCheckedChange={() => toggleDay(day_of_week)}
              disabled={disabled}
            />
            <label
              htmlFor={`working-hours-day-${day_of_week}`}
              className="text-sm text-neutral-700 cursor-pointer"
            >
              {label}
            </label>
          </div>
        ))}
      </div>

      <div className="flex items-end gap-4">
        <div className="grid gap-2">
          <Label htmlFor="working-hours-start">Start</Label>
          <Select
            value={String(value.startHour)}
            onValueChange={(v) => onChange({ ...value, startHour: Number(v) })}
            disabled={disabled}
          >
            <SelectTrigger id="working-hours-start" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOUR_OPTIONS.map((h) => (
                <SelectItem key={h} value={String(h)}>
                  {formatHour(h)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="pb-2 text-neutral-400">to</span>
        <div className="grid gap-2">
          <Label htmlFor="working-hours-end">End</Label>
          <Select
            value={String(value.endHour)}
            onValueChange={(v) => onChange({ ...value, endHour: Number(v) })}
            disabled={disabled}
          >
            <SelectTrigger id="working-hours-end" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOUR_OPTIONS.map((h) => (
                <SelectItem key={h} value={String(h)}>
                  {formatHour(h)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  )
}
