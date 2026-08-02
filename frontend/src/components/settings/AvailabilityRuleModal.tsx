// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  useCreateAvailabilityRule,
  useUpdateAvailabilityRule,
} from "@/hooks/useAvailabilityRules"
import {
  DAY_OF_WEEK_OPTIONS,
  RULE_TYPE_OPTIONS,
  type AvailabilityRuleResponse,
  type EnforcementLevel,
  type RuleType,
} from "@/types/availability"

interface AvailabilityRuleModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When supplied, the modal opens in edit mode pre-filled with this rule. */
  initialData?: AvailabilityRuleResponse
}

const ENFORCEMENT_LABELS: Record<EnforcementLevel, string> = {
  hard: "Hard — block the booking",
  soft: "Soft — allow it, but flag the conflict",
}

interface FormState {
  dayOfWeek: string
  start: string
  end: string
  max: string
  minutes: string
  startDate: string
  endDate: string
  dates: string[]
}

function emptyFormState(): FormState {
  return {
    dayOfWeek: "0",
    start: "",
    end: "",
    max: "",
    minutes: "",
    startDate: "",
    endDate: "",
    dates: [],
  }
}

function formStateFromParams(
  ruleType: RuleType,
  params: Record<string, unknown>,
): FormState {
  const state = emptyFormState()
  switch (ruleType) {
    case "working_hours":
      state.dayOfWeek = String(params.day_of_week ?? 0)
      state.start = String(params.start ?? "")
      state.end = String(params.end ?? "")
      break
    case "block_day_of_week":
      state.dayOfWeek = String(params.day_of_week ?? 0)
      break
    case "block_time_range":
      state.start = String(params.start ?? "")
      state.end = String(params.end ?? "")
      break
    case "max_per_day":
      state.max = String(params.max ?? "")
      break
    case "buffer_before":
    case "buffer_after":
      state.minutes = String(params.minutes ?? "")
      break
    case "block_date_range":
      state.startDate = String(params.start_date ?? "")
      state.endDate = String(params.end_date ?? "")
      break
    case "block_specific_dates":
      state.dates = Array.isArray(params.dates) ? params.dates.map(String) : []
      break
  }
  return state
}

/** Builds the API `params` payload for a rule type, or returns a validation error. */
function buildParams(
  ruleType: RuleType,
  state: FormState,
): { params: Record<string, unknown> } | { error: string } {
  switch (ruleType) {
    case "working_hours": {
      if (!state.start || !state.end) {
        return { error: "Start and end time are required." }
      }
      if (state.end <= state.start) {
        return { error: "End time must be after start time." }
      }
      return {
        params: {
          day_of_week: Number(state.dayOfWeek),
          start: state.start,
          end: state.end,
        },
      }
    }
    case "block_day_of_week":
      return { params: { day_of_week: Number(state.dayOfWeek) } }
    case "block_time_range": {
      if (!state.start || !state.end) {
        return { error: "Start and end time are required." }
      }
      if (state.end <= state.start) {
        return { error: "End time must be after start time." }
      }
      return { params: { start: state.start, end: state.end } }
    }
    case "max_per_day": {
      const max = Number(state.max)
      if (!state.max || !Number.isInteger(max) || max < 1) {
        return { error: "Max per day must be a whole number of at least 1." }
      }
      return { params: { max } }
    }
    case "buffer_before":
    case "buffer_after": {
      const minutes = Number(state.minutes)
      if (state.minutes === "" || !Number.isInteger(minutes) || minutes < 0) {
        return { error: "Buffer minutes must be a whole number of 0 or more." }
      }
      return { params: { minutes } }
    }
    case "block_date_range": {
      if (!state.startDate || !state.endDate) {
        return { error: "Start and end date are required." }
      }
      if (state.endDate < state.startDate) {
        return { error: "End date must be on or after the start date." }
      }
      return { params: { start_date: state.startDate, end_date: state.endDate } }
    }
    case "block_specific_dates": {
      if (state.dates.length === 0) {
        return { error: "Add at least one date." }
      }
      return { params: { dates: state.dates } }
    }
  }
}

export function AvailabilityRuleModal({
  open,
  onOpenChange,
  initialData,
}: AvailabilityRuleModalProps) {
  const isEdit = !!initialData
  const initialRuleType = (initialData?.rule_type as RuleType) ?? "working_hours"

  const [ruleType, setRuleType] = useState<RuleType>(initialRuleType)
  const [enforcement, setEnforcement] = useState<EnforcementLevel>(
    (initialData?.enforcement as EnforcementLevel) ?? "hard",
  )
  const [form, setForm] = useState<FormState>(
    initialData
      ? formStateFromParams(initialRuleType, initialData.params)
      : emptyFormState(),
  )
  const [newDate, setNewDate] = useState("")
  const [error, setError] = useState<string | null>(null)

  const createRule = useCreateAvailabilityRule()
  const updateRule = useUpdateAvailabilityRule()

  const isPending = createRule.isPending || updateRule.isPending

  function resetForm() {
    setRuleType(initialRuleType)
    setEnforcement((initialData?.enforcement as EnforcementLevel) ?? "hard")
    setForm(
      initialData
        ? formStateFromParams(initialRuleType, initialData.params)
        : emptyFormState(),
    )
    setNewDate("")
    setError(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) resetForm()
    onOpenChange(next)
  }

  function handleRuleTypeChange(value: string) {
    setRuleType(value as RuleType)
    setForm(emptyFormState())
    setError(null)
  }

  function handleAddDate() {
    if (!newDate) return
    if (form.dates.includes(newDate)) {
      setNewDate("")
      return
    }
    setForm({ ...form, dates: [...form.dates, newDate].sort() })
    setNewDate("")
  }

  function handleRemoveDate(date: string) {
    setForm({ ...form, dates: form.dates.filter((d) => d !== date) })
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    const result = buildParams(ruleType, form)
    if ("error" in result) {
      setError(result.error)
      return
    }

    try {
      if (isEdit) {
        await updateRule.mutateAsync({
          ruleId: initialData.id,
          data: { enforcement, params: result.params },
        })
      } else {
        await createRule.mutateAsync({
          rule_type: ruleType,
          enforcement,
          params: result.params,
        })
      }
      onOpenChange(false)
      resetForm()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not save the availability rule. Please try again.",
      )
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit rule" : "Add availability rule"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="rule-type">Rule type</Label>
            {isEdit ? (
              <p id="rule-type" className="text-sm font-medium text-neutral-900">
                {RULE_TYPE_OPTIONS.find((r) => r.value === ruleType)?.label}
              </p>
            ) : (
              <Select value={ruleType} onValueChange={handleRuleTypeChange}>
                <SelectTrigger id="rule-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RULE_TYPE_OPTIONS.map((r) => (
                    <SelectItem key={r.value} value={r.value}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <p className="text-xs text-neutral-500">
              {RULE_TYPE_OPTIONS.find((r) => r.value === ruleType)?.description}
            </p>
          </div>

          {(ruleType === "working_hours" || ruleType === "block_day_of_week") && (
            <div className="space-y-1.5">
              <Label htmlFor="rule-day-of-week">Day of week</Label>
              <Select
                value={form.dayOfWeek}
                onValueChange={(v) => setForm({ ...form, dayOfWeek: v })}
              >
                <SelectTrigger id="rule-day-of-week" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DAY_OF_WEEK_OPTIONS.map((d) => (
                    <SelectItem key={d.value} value={String(d.value)}>
                      {d.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {(ruleType === "working_hours" || ruleType === "block_time_range") && (
            <div className="flex gap-4">
              <div className="space-y-1.5 flex-1">
                <Label htmlFor="rule-start">Start time</Label>
                <Input
                  id="rule-start"
                  type="time"
                  value={form.start}
                  onChange={(e) => setForm({ ...form, start: e.target.value })}
                />
              </div>
              <div className="space-y-1.5 flex-1">
                <Label htmlFor="rule-end">End time</Label>
                <Input
                  id="rule-end"
                  type="time"
                  value={form.end}
                  onChange={(e) => setForm({ ...form, end: e.target.value })}
                />
              </div>
            </div>
          )}

          {ruleType === "max_per_day" && (
            <div className="space-y-1.5">
              <Label htmlFor="rule-max">Max appointments per day</Label>
              <Input
                id="rule-max"
                type="number"
                min={1}
                step={1}
                value={form.max}
                onChange={(e) => setForm({ ...form, max: e.target.value })}
              />
            </div>
          )}

          {(ruleType === "buffer_before" || ruleType === "buffer_after") && (
            <div className="space-y-1.5">
              <Label htmlFor="rule-minutes">Buffer minutes</Label>
              <Input
                id="rule-minutes"
                type="number"
                min={0}
                step={1}
                value={form.minutes}
                onChange={(e) => setForm({ ...form, minutes: e.target.value })}
              />
            </div>
          )}

          {ruleType === "block_date_range" && (
            <div className="flex gap-4">
              <div className="space-y-1.5 flex-1">
                <Label htmlFor="rule-start-date">Start date</Label>
                <Input
                  id="rule-start-date"
                  type="date"
                  value={form.startDate}
                  onChange={(e) => setForm({ ...form, startDate: e.target.value })}
                />
              </div>
              <div className="space-y-1.5 flex-1">
                <Label htmlFor="rule-end-date">End date</Label>
                <Input
                  id="rule-end-date"
                  type="date"
                  value={form.endDate}
                  onChange={(e) => setForm({ ...form, endDate: e.target.value })}
                />
              </div>
            </div>
          )}

          {ruleType === "block_specific_dates" && (
            <div className="space-y-1.5">
              <Label htmlFor="rule-new-date">Dates</Label>
              <div className="flex gap-2">
                <Input
                  id="rule-new-date"
                  type="date"
                  value={newDate}
                  onChange={(e) => setNewDate(e.target.value)}
                />
                <Button type="button" variant="outline" onClick={handleAddDate}>
                  Add date
                </Button>
              </div>
              {form.dates.length > 0 && (
                <ul className="flex flex-wrap gap-2 pt-1">
                  {form.dates.map((date) => (
                    <li
                      key={date}
                      className="flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-700"
                    >
                      {date}
                      <button
                        type="button"
                        onClick={() => handleRemoveDate(date)}
                        aria-label={`Remove ${date}`}
                        className="text-neutral-500 hover:text-red-600"
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="rule-enforcement">Enforcement</Label>
            <Select
              value={enforcement}
              onValueChange={(v) => setEnforcement(v as EnforcementLevel)}
            >
              <SelectTrigger id="rule-enforcement" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(ENFORCEMENT_LABELS) as EnforcementLevel[]).map((level) => (
                  <SelectItem key={level} value={level}>
                    {ENFORCEMENT_LABELS[level]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-neutral-500">
              Hard rules always block a conflicting booking; soft rules let it
              through but flag the conflict for review.
            </p>
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? "Saving…" : isEdit ? "Save changes" : "Add rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
