// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useAvailabilityRules,
  useCreateAvailabilityRule,
  useUpdateAvailabilityRule,
  useDeleteAvailabilityRule,
} from "@/hooks/useAvailability"
import { usePreferences } from "@/hooks/usePreferences"
import type { AvailabilityRule } from "@/types/availability"
import { deriveWorkingHoursWindow, formatClockTime, timezoneAbbreviation } from "@/lib/workingHours"
import { Toggle } from "./ui"
import { cn } from "@/lib/utils"

const DAYS = [
  { dayOfWeek: 0, label: "Monday" },
  { dayOfWeek: 1, label: "Tuesday" },
  { dayOfWeek: 2, label: "Wednesday" },
  { dayOfWeek: 3, label: "Thursday" },
  { dayOfWeek: 4, label: "Friday" },
  { dayOfWeek: 5, label: "Saturday" },
  { dayOfWeek: 6, label: "Sunday" },
] as const

const AXIS_START_HOUR = 6
const AXIS_END_HOUR = 22

/** Every half hour from 6 AM to 10 PM, the grid's axis. */
const TIME_OPTIONS = Array.from({ length: (AXIS_END_HOUR - AXIS_START_HOUR) * 2 + 1 }, (_, i) => {
  const totalMinutes = AXIS_START_HOUR * 60 + i * 30
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`
})

const DEFAULT_START = "09:00"
const DEFAULT_END = "17:00"

function barPercent(time: string): number {
  const [h, m] = time.split(":").map(Number)
  const hours = h + m / 60
  return Math.max(0, Math.min(100, ((hours - AXIS_START_HOUR) / (AXIS_END_HOUR - AXIS_START_HOUR)) * 100))
}

interface DayRuleMap {
  [dayOfWeek: number]: AvailabilityRule
}

/**
 * Practice > Availability's "Working hours" card: a seven-row week grid
 * backed directly by `working_hours` availability rules — one rule per
 * enabled day. Toggling a day off deletes its rule; toggling on creates one
 * with the default 9-5 times. This is the friendly path for the one rule
 * type common to every practice; every other rule type still goes through
 * the generic rules engine and `RuleForm`.
 */
export function WorkingHoursGrid() {
  const { data, isLoading } = useAvailabilityRules()
  const { data: preferences } = usePreferences()
  const createMutation = useCreateAvailabilityRule()
  const updateMutation = useUpdateAvailabilityRule()
  const deleteMutation = useDeleteAvailabilityRule()

  const rules = data?.data ?? []
  const workingHoursRules = rules.filter((r) => r.rule_type === "working_hours")
  const ruleByDay: DayRuleMap = {}
  for (const rule of workingHoursRules) {
    const day = Number(rule.params.day_of_week)
    ruleByDay[day] = rule
  }
  const isSaving = createMutation.isPending || updateMutation.isPending || deleteMutation.isPending
  const window = deriveWorkingHoursWindow(rules)

  function handleToggle(dayOfWeek: number, on: boolean) {
    const existing = ruleByDay[dayOfWeek]
    if (on) {
      if (existing) return
      createMutation.mutate({
        rule_type: "working_hours",
        enforcement: "hard",
        params: { day_of_week: dayOfWeek, start: DEFAULT_START, end: DEFAULT_END },
      })
    } else {
      if (!existing) return
      deleteMutation.mutate(existing.id)
    }
  }

  function handleTimeChange(dayOfWeek: number, field: "start" | "end", value: string) {
    const existing = ruleByDay[dayOfWeek]
    if (!existing) return
    const start = field === "start" ? value : String(existing.params.start)
    const end = field === "end" ? value : String(existing.params.end)
    if (end <= start) return
    updateMutation.mutate({ ruleId: existing.id, data: { params: { day_of_week: dayOfWeek, start, end } } })
  }

  function handleSeedWeekdays() {
    for (const { dayOfWeek } of DAYS) {
      if (dayOfWeek > 4) continue
      if (ruleByDay[dayOfWeek]) continue
      createMutation.mutate({
        rule_type: "working_hours",
        enforcement: "hard",
        params: { day_of_week: dayOfWeek, start: DEFAULT_START, end: DEFAULT_END },
      })
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {workingHoursRules.length === 0 && (
        <Button size="sm" variant="outline" onClick={handleSeedWeekdays} disabled={isSaving}>
          Set Monday to Friday, 9 to 5
        </Button>
      )}

      <div className="space-y-2">
        {DAYS.map(({ dayOfWeek, label }) => {
          const rule = ruleByDay[dayOfWeek]
          const on = !!rule
          const start = rule ? String(rule.params.start) : DEFAULT_START
          const end = rule ? String(rule.params.end) : DEFAULT_END

          return (
            <div key={dayOfWeek} className="flex items-center gap-3">
              <span
                className={cn("w-20 shrink-0 text-sm font-medium", !on && "text-muted-foreground")}
              >
                {label}
              </span>
              <Toggle
                checked={on}
                onChange={(next) => handleToggle(dayOfWeek, next)}
                label={`${label} on`}
                disabled={isSaving}
              />
              <div
                aria-hidden="true"
                className={cn("relative h-2 flex-1 rounded-full", on ? "bg-foreground/10" : "bg-foreground/5")}
              >
                {on && (
                  <span
                    className="absolute inset-y-0 rounded-full bg-primary-400"
                    style={{ left: `${barPercent(start)}%`, width: `${barPercent(end) - barPercent(start)}%` }}
                  />
                )}
              </div>
              {on ? (
                <div className="flex shrink-0 items-center gap-1.5">
                  <Select
                    value={start}
                    onValueChange={(v) => handleTimeChange(dayOfWeek, "start", v)}
                    disabled={isSaving}
                  >
                    <SelectTrigger aria-label={`${label} start`} className="h-8 w-[92px] text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TIME_OPTIONS.map((t) => (
                        <SelectItem key={t} value={t}>
                          {formatClockTime(t)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <span className="text-xs text-muted-foreground">to</span>
                  <Select
                    value={end}
                    onValueChange={(v) => handleTimeChange(dayOfWeek, "end", v)}
                    disabled={isSaving}
                  >
                    <SelectTrigger aria-label={`${label} end`} className="h-8 w-[92px] text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TIME_OPTIONS.map((t) => (
                        <SelectItem key={t} value={t}>
                          {formatClockTime(t)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : (
                <span className="w-[210px] shrink-0 text-xs text-muted-foreground">Not available</span>
              )}
            </div>
          )
        })}
      </div>

      <div aria-hidden="true" className="flex justify-between pl-[128px] text-[10px] text-muted-foreground">
        <span>6 AM</span>
        <span>10 AM</span>
        <span>2 PM</span>
        <span>6 PM</span>
        <span>10 PM</span>
      </div>

      <p data-testid="working-hours-footer" className="text-[13px] text-muted-foreground">
        {window ? (
          <>
            Calendar shows{" "}
            <span className="font-semibold text-foreground">
              {formatClockTime(window.earliestStart)} – {formatClockTime(window.latestEnd)}
            </span>
            {preferences && <>, in {timezoneAbbreviation(preferences.timezone)}</>}.{" "}
          </>
        ) : (
          <>No working hours set yet. </>
        )}
        <Link href="/dashboard/settings/profile" className="font-semibold text-foreground underline">
          Change timezone
        </Link>
      </p>
    </div>
  )
}
