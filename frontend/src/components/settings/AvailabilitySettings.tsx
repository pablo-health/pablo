// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useAvailabilityRules,
  useCreateAvailabilityRule,
  useUpdateAvailabilityRule,
  useDeleteAvailabilityRule,
} from "@/hooks/useAvailability"
import { ApiError } from "@/lib/api/client"
import { RULE_TYPES, ENFORCEMENT_LEVELS } from "@/types/availability"
import type {
  AvailabilityRule,
  EnforcementLevel,
  RuleType,
} from "@/types/availability"

const DAY_OPTIONS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
]

function dayLabel(day: unknown): string {
  return DAY_OPTIONS.find((d) => d.value === Number(day))?.label ?? "Unknown day"
}

export const RULE_TYPE_LABELS: Record<RuleType, string> = {
  working_hours: "Working hours",
  block_day_of_week: "Block a day of the week",
  block_time_range: "Block a time range",
  max_per_day: "Limit appointments per day",
  buffer_before: "Buffer before appointments",
  buffer_after: "Buffer after appointments",
  block_date_range: "Block a date range",
  block_specific_dates: "Block specific dates",
  session_defaults: "Scheduling defaults",
}

// The rule-type picker in RuleForm offers every type except session_defaults,
// which has its own dedicated fields section — one editing surface only.
const PICKER_RULE_TYPES = RULE_TYPES.filter((rt) => rt !== "session_defaults")

type RuleGroup = "Working hours" | "Blocked time" | "Limits & buffers"

const RULE_TYPE_GROUPS: Record<RuleType, RuleGroup> = {
  working_hours: "Working hours",
  block_day_of_week: "Blocked time",
  block_time_range: "Blocked time",
  block_date_range: "Blocked time",
  block_specific_dates: "Blocked time",
  max_per_day: "Limits & buffers",
  buffer_before: "Limits & buffers",
  buffer_after: "Limits & buffers",
  session_defaults: "Limits & buffers",
}

const GROUP_ORDER: RuleGroup[] = ["Working hours", "Blocked time", "Limits & buffers"]

type ParamFields = Record<string, string>

function defaultFields(ruleType: RuleType): ParamFields {
  switch (ruleType) {
    case "working_hours":
      return { day_of_week: "0", start: "09:00", end: "17:00" }
    case "block_day_of_week":
      return { day_of_week: "6" }
    case "block_time_range":
      return { start: "12:00", end: "13:00" }
    case "max_per_day":
      return { max: "8" }
    case "buffer_before":
    case "buffer_after":
      return { minutes: "15" }
    case "block_date_range":
      return { start_date: "", end_date: "" }
    case "block_specific_dates":
      return {}
    case "session_defaults":
      return {}
  }
}

function paramsToFields(
  ruleType: RuleType,
  params: Record<string, unknown>
): { fields: ParamFields; dates: string[] } {
  switch (ruleType) {
    case "working_hours":
      return {
        fields: {
          day_of_week: String(params.day_of_week ?? 0),
          start: String(params.start ?? "09:00"),
          end: String(params.end ?? "17:00"),
        },
        dates: [],
      }
    case "block_day_of_week":
      return { fields: { day_of_week: String(params.day_of_week ?? 0) }, dates: [] }
    case "block_time_range":
      return {
        fields: { start: String(params.start ?? ""), end: String(params.end ?? "") },
        dates: [],
      }
    case "max_per_day":
      return { fields: { max: String(params.max ?? 1) }, dates: [] }
    case "buffer_before":
    case "buffer_after":
      return { fields: { minutes: String(params.minutes ?? 0) }, dates: [] }
    case "block_date_range":
      return {
        fields: {
          start_date: String(params.start_date ?? ""),
          end_date: String(params.end_date ?? ""),
        },
        dates: [],
      }
    case "block_specific_dates":
      return {
        fields: {},
        dates: Array.isArray(params.dates) ? params.dates.map(String) : [],
      }
    case "session_defaults":
      return { fields: {}, dates: [] }
  }
}

function buildParams(
  ruleType: RuleType,
  fields: ParamFields,
  dates: string[]
): Record<string, unknown> {
  switch (ruleType) {
    case "working_hours":
      return {
        day_of_week: Number(fields.day_of_week),
        start: fields.start,
        end: fields.end,
      }
    case "block_day_of_week":
      return { day_of_week: Number(fields.day_of_week) }
    case "block_time_range":
      return { start: fields.start, end: fields.end }
    case "max_per_day":
      return { max: Number(fields.max) }
    case "buffer_before":
    case "buffer_after":
      return { minutes: Number(fields.minutes) }
    case "block_date_range":
      return { start_date: fields.start_date, end_date: fields.end_date }
    case "block_specific_dates":
      return { dates }
    case "session_defaults":
      return {}
  }
}

function validate(ruleType: RuleType, fields: ParamFields, dates: string[]): string | null {
  switch (ruleType) {
    case "working_hours":
    case "block_time_range":
      if (!fields.start || !fields.end) return "Start and end times are required."
      if (fields.end <= fields.start) return "End time must be after start time."
      return null
    case "block_date_range":
      if (!fields.start_date || !fields.end_date) return "Start and end dates are required."
      if (fields.end_date < fields.start_date) {
        return "End date must be on or after the start date."
      }
      return null
    case "block_specific_dates":
      if (dates.length === 0) return "Add at least one date."
      return null
    case "max_per_day":
      if (fields.max === "" || Number(fields.max) < 1) return "Maximum must be at least 1."
      return null
    case "buffer_before":
    case "buffer_after":
      if (fields.minutes === "" || Number(fields.minutes) < 0) {
        return "Buffer minutes cannot be negative."
      }
      return null
    case "block_day_of_week":
      return null
    case "session_defaults":
      return null
  }
}

export function summarize(rule: AvailabilityRule): string {
  const p = rule.params
  switch (rule.rule_type) {
    case "working_hours":
      return `${dayLabel(p.day_of_week)} · ${p.start}–${p.end}`
    case "block_day_of_week":
      return `${dayLabel(p.day_of_week)} blocked`
    case "block_time_range":
      return `${p.start}–${p.end} blocked every day`
    case "max_per_day": {
      const max = Number(p.max)
      return `Max ${max} appointment${max === 1 ? "" : "s"} per day`
    }
    case "buffer_before":
      return `${p.minutes} min buffer before every appointment`
    case "buffer_after":
      return `${p.minutes} min buffer after every appointment`
    case "block_date_range":
      return `${p.start_date} to ${p.end_date} blocked`
    case "block_specific_dates":
      return `${(Array.isArray(p.dates) ? p.dates : []).join(", ")} blocked`
    case "session_defaults": {
      const parts: string[] = []
      if (p.duration_minutes != null) parts.push(`${p.duration_minutes} min sessions`)
      if (p.alignment === "hour") parts.push("on the hour")
      if (p.alignment === "half_hour") parts.push("on the half hour")
      return parts.length > 0 ? parts.join(", ") : "No scheduling defaults set"
    }
    default:
      return ""
  }
}

// --- Scheduling defaults: session length, break, and start-time alignment ---
//
// These three fields map onto a single session_defaults rule (length +
// alignment) and the existing buffer_after rule (break) rather than any new
// storage. Kept as pure functions so the mapping is unit-testable without
// rendering the form.

export type SessionAlignment = "hour" | "half_hour" | "none"

export interface SchedulingDefaultsFields {
  durationMinutes: string
  breakMinutes: string
  alignment: SessionAlignment
}

export function schedulingDefaultsFromRules(rules: AvailabilityRule[]): SchedulingDefaultsFields {
  const sessionDefaults = rules.find((r) => r.rule_type === "session_defaults")
  const bufferAfter = rules.find((r) => r.rule_type === "buffer_after")
  const alignment = sessionDefaults?.params.alignment
  return {
    durationMinutes:
      sessionDefaults?.params.duration_minutes != null
        ? String(sessionDefaults.params.duration_minutes)
        : "",
    breakMinutes: bufferAfter?.params.minutes != null ? String(bufferAfter.params.minutes) : "0",
    alignment: alignment === "hour" || alignment === "half_hour" ? alignment : "none",
  }
}

export interface SchedulingDefaultsPayloads {
  sessionDefaultsParams: Record<string, unknown>
  breakMinutes: number
}

export function schedulingDefaultsToRulePayloads(
  fields: SchedulingDefaultsFields
): SchedulingDefaultsPayloads {
  const sessionDefaultsParams: Record<string, unknown> = {}
  if (fields.durationMinutes !== "") {
    sessionDefaultsParams.duration_minutes = Number(fields.durationMinutes)
  }
  if (fields.alignment !== "none") {
    sessionDefaultsParams.alignment = fields.alignment
  }
  return {
    sessionDefaultsParams,
    breakMinutes: fields.breakMinutes === "" ? 0 : Number(fields.breakMinutes),
  }
}

interface SchedulingDefaultsSectionProps {
  rules: AvailabilityRule[]
  onSave: (fields: SchedulingDefaultsFields) => void
  isSaving: boolean
  error: string | null
}

function SchedulingDefaultsSection({
  rules,
  onSave,
  isSaving,
  error,
}: SchedulingDefaultsSectionProps) {
  const initial = schedulingDefaultsFromRules(rules)
  const [durationMinutes, setDurationMinutes] = useState(initial.durationMinutes)
  const [breakMinutes, setBreakMinutes] = useState(initial.breakMinutes)
  const [alignment, setAlignment] = useState<SessionAlignment>(initial.alignment)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSave({ durationMinutes, breakMinutes, alignment })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-4 rounded-md border border-neutral-200 p-4"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
        Scheduling defaults
      </h3>
      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-2">
          <Label htmlFor="session-length">Session length (minutes)</Label>
          <Input
            id="session-length"
            type="number"
            min={1}
            placeholder="50"
            value={durationMinutes}
            onChange={(e) => setDurationMinutes(e.target.value)}
            disabled={isSaving}
            className="w-32"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="session-break">Break between sessions (minutes)</Label>
          <Input
            id="session-break"
            type="number"
            min={0}
            value={breakMinutes}
            onChange={(e) => setBreakMinutes(e.target.value)}
            disabled={isSaving}
            className="w-32"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="session-alignment">Start-time alignment</Label>
          <Select
            value={alignment}
            onValueChange={(v) => setAlignment(v as SessionAlignment)}
            disabled={isSaving}
          >
            <SelectTrigger id="session-alignment" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="hour">On the hour</SelectItem>
              <SelectItem value="half_hour">On the half hour</SelectItem>
              <SelectItem value="none">No alignment</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <Button type="submit" size="sm" disabled={isSaving}>
        {isSaving ? "Saving..." : "Save scheduling defaults"}
      </Button>
    </form>
  )
}

interface RuleParamsFieldsProps {
  ruleType: RuleType
  fields: ParamFields
  onFieldChange: (key: string, value: string) => void
  dates: string[]
  newDate: string
  onNewDateChange: (value: string) => void
  onAddDate: () => void
  onRemoveDate: (date: string) => void
  disabled: boolean
}

function RuleParamsFields({
  ruleType,
  fields,
  onFieldChange,
  dates,
  newDate,
  onNewDateChange,
  onAddDate,
  onRemoveDate,
  disabled,
}: RuleParamsFieldsProps) {
  switch (ruleType) {
    case "working_hours":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div className="grid gap-2">
            <Label htmlFor="param-day">Day</Label>
            <Select
              value={fields.day_of_week}
              onValueChange={(v) => onFieldChange("day_of_week", v)}
              disabled={disabled}
            >
              <SelectTrigger id="param-day" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DAY_OPTIONS.map((d) => (
                  <SelectItem key={d.value} value={String(d.value)}>
                    {d.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="param-start">Start</Label>
            <Input
              id="param-start"
              type="time"
              value={fields.start ?? ""}
              onChange={(e) => onFieldChange("start", e.target.value)}
              disabled={disabled}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="param-end">End</Label>
            <Input
              id="param-end"
              type="time"
              value={fields.end ?? ""}
              onChange={(e) => onFieldChange("end", e.target.value)}
              disabled={disabled}
            />
          </div>
        </div>
      )
    case "block_day_of_week":
      return (
        <div className="grid gap-2 max-w-xs">
          <Label htmlFor="param-day">Day to block</Label>
          <Select
            value={fields.day_of_week}
            onValueChange={(v) => onFieldChange("day_of_week", v)}
            disabled={disabled}
          >
            <SelectTrigger id="param-day">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DAY_OPTIONS.map((d) => (
                <SelectItem key={d.value} value={String(d.value)}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )
    case "block_time_range":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div className="grid gap-2">
            <Label htmlFor="param-start">Start</Label>
            <Input
              id="param-start"
              type="time"
              value={fields.start ?? ""}
              onChange={(e) => onFieldChange("start", e.target.value)}
              disabled={disabled}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="param-end">End</Label>
            <Input
              id="param-end"
              type="time"
              value={fields.end ?? ""}
              onChange={(e) => onFieldChange("end", e.target.value)}
              disabled={disabled}
            />
          </div>
        </div>
      )
    case "max_per_day":
      return (
        <div className="grid gap-2 max-w-xs">
          <Label htmlFor="param-max">Max appointments per day</Label>
          <Input
            id="param-max"
            type="number"
            min={1}
            value={fields.max ?? ""}
            onChange={(e) => onFieldChange("max", e.target.value)}
            disabled={disabled}
          />
        </div>
      )
    case "buffer_before":
      return (
        <div className="grid gap-2 max-w-xs">
          <Label htmlFor="param-minutes">Buffer minutes before an appointment</Label>
          <Input
            id="param-minutes"
            type="number"
            min={0}
            value={fields.minutes ?? ""}
            onChange={(e) => onFieldChange("minutes", e.target.value)}
            disabled={disabled}
          />
        </div>
      )
    case "buffer_after":
      return (
        <div className="grid gap-2 max-w-xs">
          <Label htmlFor="param-minutes">Buffer minutes after an appointment</Label>
          <Input
            id="param-minutes"
            type="number"
            min={0}
            value={fields.minutes ?? ""}
            onChange={(e) => onFieldChange("minutes", e.target.value)}
            disabled={disabled}
          />
        </div>
      )
    case "block_date_range":
      return (
        <div className="flex flex-wrap items-end gap-4">
          <div className="grid gap-2">
            <Label htmlFor="param-start-date">Start date</Label>
            <Input
              id="param-start-date"
              type="date"
              value={fields.start_date ?? ""}
              onChange={(e) => onFieldChange("start_date", e.target.value)}
              disabled={disabled}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="param-end-date">End date</Label>
            <Input
              id="param-end-date"
              type="date"
              value={fields.end_date ?? ""}
              onChange={(e) => onFieldChange("end_date", e.target.value)}
              disabled={disabled}
            />
          </div>
        </div>
      )
    case "block_specific_dates":
      return (
        <div className="grid gap-2 max-w-sm">
          <Label htmlFor="param-new-date">Dates to block</Label>
          <div className="flex gap-2">
            <Input
              id="param-new-date"
              type="date"
              value={newDate}
              onChange={(e) => onNewDateChange(e.target.value)}
              disabled={disabled}
            />
            <Button type="button" size="sm" variant="outline" onClick={onAddDate} disabled={disabled}>
              Add date
            </Button>
          </div>
          {dates.length > 0 && (
            <ul className="flex flex-wrap gap-2">
              {dates.map((d) => (
                <li
                  key={d}
                  className="flex items-center gap-1 rounded-full bg-neutral-100 px-2 py-1 text-xs text-neutral-700"
                >
                  {d}
                  <button
                    type="button"
                    aria-label={`Remove ${d}`}
                    onClick={() => onRemoveDate(d)}
                    disabled={disabled}
                    className="text-neutral-500 hover:text-neutral-900"
                  >
                    &times;
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )
    case "session_defaults":
      // Owned by the dedicated "Scheduling defaults" fields section above —
      // never reachable here since PICKER_RULE_TYPES excludes it.
      return null
  }
}

export interface RuleFormProps {
  initialRule: AvailabilityRule | null
  onCancel: () => void
  onSubmit: (ruleType: RuleType, enforcement: EnforcementLevel, params: Record<string, unknown>) => void
  isSaving: boolean
  submitError: string | null
}

export function RuleForm({ initialRule, onCancel, onSubmit, isSaving, submitError }: RuleFormProps) {
  const [ruleType, setRuleType] = useState<RuleType>(initialRule?.rule_type ?? "working_hours")
  const [enforcement, setEnforcement] = useState<EnforcementLevel>(initialRule?.enforcement ?? "hard")
  const initialState = initialRule
    ? paramsToFields(initialRule.rule_type, initialRule.params)
    : { fields: defaultFields("working_hours"), dates: [] }
  const [fields, setFields] = useState<ParamFields>(initialState.fields)
  const [dates, setDates] = useState<string[]>(initialState.dates)
  const [newDate, setNewDate] = useState("")
  const [validationError, setValidationError] = useState<string | null>(null)

  function handleRuleTypeChange(value: string) {
    const nextType = value as RuleType
    setRuleType(nextType)
    setFields(defaultFields(nextType))
    setDates([])
    setValidationError(null)
  }

  function setField(key: string, value: string) {
    setFields((prev) => ({ ...prev, [key]: value }))
  }

  function addDate() {
    if (!newDate || dates.includes(newDate)) return
    setDates([...dates, newDate].sort())
    setNewDate("")
  }

  function removeDate(date: string) {
    setDates(dates.filter((d) => d !== date))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const error = validate(ruleType, fields, dates)
    if (error) {
      setValidationError(error)
      return
    }
    setValidationError(null)
    onSubmit(ruleType, enforcement, buildParams(ruleType, fields, dates))
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 rounded-md border border-neutral-200 p-4">
      <div className="grid gap-2 max-w-xs">
        <Label htmlFor="rule-type">Rule type</Label>
        <Select
          value={ruleType}
          onValueChange={handleRuleTypeChange}
          disabled={isSaving || !!initialRule}
        >
          <SelectTrigger id="rule-type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PICKER_RULE_TYPES.map((rt) => (
              <SelectItem key={rt} value={rt}>
                {RULE_TYPE_LABELS[rt]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <RuleParamsFields
        ruleType={ruleType}
        fields={fields}
        onFieldChange={setField}
        dates={dates}
        newDate={newDate}
        onNewDateChange={setNewDate}
        onAddDate={addDate}
        onRemoveDate={removeDate}
        disabled={isSaving}
      />

      <div className="grid gap-2 max-w-xs">
        <Label htmlFor="enforcement">Enforcement</Label>
        <Select
          value={enforcement}
          onValueChange={(v) => setEnforcement(v as EnforcementLevel)}
          disabled={isSaving}
        >
          <SelectTrigger id="enforcement">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ENFORCEMENT_LEVELS.map((level) => (
              <SelectItem key={level} value={level}>
                {level === "hard" ? "Hard" : "Soft"}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-sm text-neutral-600">
          Hard rules always block a conflicting appointment; soft rules only warn but still let you book.
        </p>
      </div>

      {validationError && (
        <p role="alert" className="text-sm text-red-600">
          {validationError}
        </p>
      )}
      {submitError && (
        <p role="alert" className="text-sm text-red-600">
          {submitError}
        </p>
      )}

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={isSaving}>
          {isSaving ? "Saving..." : initialRule ? "Save changes" : "Add rule"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={isSaving}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return "Something went wrong. Please try again."
}

export function AvailabilitySettings() {
  const { data, isLoading, error } = useAvailabilityRules()
  const createMutation = useCreateAvailabilityRule()
  const updateMutation = useUpdateAvailabilityRule()
  const deleteMutation = useDeleteAvailabilityRule()

  const [formOpen, setFormOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<AvailabilityRule | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [schedulingDefaultsError, setSchedulingDefaultsError] = useState<string | null>(null)

  const rules = data?.data ?? []
  // session_defaults is edited exclusively through the dedicated fields
  // section above — keep it out of the generic grouped rule list.
  const visibleRules = rules.filter((rule) => rule.rule_type !== "session_defaults")

  function openCreateForm() {
    setEditingRule(null)
    setFormError(null)
    setFormOpen(true)
  }

  function openEditForm(rule: AvailabilityRule) {
    setEditingRule(rule)
    setFormError(null)
    setFormOpen(true)
  }

  function closeForm() {
    setFormOpen(false)
    setEditingRule(null)
    setFormError(null)
  }

  function handleFormSubmit(
    ruleType: RuleType,
    enforcement: EnforcementLevel,
    params: Record<string, unknown>
  ) {
    if (editingRule) {
      updateMutation.mutate(
        { ruleId: editingRule.id, data: { rule_type: ruleType, enforcement, params } },
        {
          onSuccess: closeForm,
          onError: (err) => setFormError(errorMessage(err)),
        }
      )
    } else {
      createMutation.mutate(
        { rule_type: ruleType, enforcement, params },
        {
          onSuccess: closeForm,
          onError: (err) => setFormError(errorMessage(err)),
        }
      )
    }
  }

  function handleDelete(rule: AvailabilityRule) {
    if (!window.confirm(`Delete this rule? ${summarize(rule)}`)) return
    setListError(null)
    deleteMutation.mutate(rule.id, {
      onError: (err) => setListError(errorMessage(err)),
    })
  }

  function handleSchedulingDefaultsSave(fields: SchedulingDefaultsFields) {
    setSchedulingDefaultsError(null)
    const { sessionDefaultsParams, breakMinutes } = schedulingDefaultsToRulePayloads(fields)
    const existingSessionDefaults = rules.find((r) => r.rule_type === "session_defaults")
    const existingBufferAfter = rules.find((r) => r.rule_type === "buffer_after")
    const onError = (err: unknown) => setSchedulingDefaultsError(errorMessage(err))

    if (existingSessionDefaults) {
      updateMutation.mutate(
        { ruleId: existingSessionDefaults.id, data: { params: sessionDefaultsParams } },
        { onError }
      )
    } else {
      createMutation.mutate(
        { rule_type: "session_defaults", enforcement: "soft", params: sessionDefaultsParams },
        { onError }
      )
    }

    if (breakMinutes > 0) {
      const params = { minutes: breakMinutes }
      if (existingBufferAfter) {
        updateMutation.mutate({ ruleId: existingBufferAfter.id, data: { params } }, { onError })
      } else {
        createMutation.mutate({ rule_type: "buffer_after", enforcement: "hard", params }, { onError })
      }
    } else if (existingBufferAfter) {
      deleteMutation.mutate(existingBufferAfter.id, { onError })
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  if (error) {
    return <p className="text-sm text-red-600">Failed to load availability rules.</p>
  }

  const activeGroups = GROUP_ORDER.filter((group) =>
    visibleRules.some((rule) => RULE_TYPE_GROUPS[rule.rule_type] === group)
  )

  return (
    <div className="space-y-4">
      <SchedulingDefaultsSection
        rules={rules}
        onSave={handleSchedulingDefaultsSave}
        isSaving={createMutation.isPending || updateMutation.isPending || deleteMutation.isPending}
        error={schedulingDefaultsError}
      />

      {visibleRules.length === 0 && (
        <div className="rounded-md border border-dashed border-neutral-300 p-6 text-center">
          <p className="text-sm text-neutral-600">
            You don&apos;t have any availability rules yet. A rule controls when
            appointments can be booked — for example blocking Fridays, capping
            how many sessions you take in a day, or requiring a buffer between
            back-to-back sessions.
          </p>
        </div>
      )}

      {activeGroups.map((group) => (
        <div key={group}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            {group}
          </h3>
          <ul className="space-y-2">
            {visibleRules
              .filter((rule) => RULE_TYPE_GROUPS[rule.rule_type] === group)
              .map((rule) => (
                <li
                  key={rule.id}
                  className="flex items-center justify-between rounded-md border border-neutral-200 px-3 py-2"
                >
                  <div>
                    <p className="text-sm font-medium text-neutral-900">
                      {RULE_TYPE_LABELS[rule.rule_type]}
                    </p>
                    <p className="text-sm text-neutral-600">{summarize(rule)}</p>
                    <p className="text-xs text-neutral-500">
                      {rule.enforcement === "hard" ? "Hard — always enforced" : "Soft — allows override"}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => openEditForm(rule)}>
                      Edit
                    </Button>
                    <Button size="sm" variant="destructive" onClick={() => handleDelete(rule)}>
                      Delete
                    </Button>
                  </div>
                </li>
              ))}
          </ul>
        </div>
      ))}

      {listError && (
        <p role="alert" className="text-sm text-red-600">
          {listError}
        </p>
      )}

      {formOpen ? (
        <RuleForm
          initialRule={editingRule}
          onCancel={closeForm}
          onSubmit={handleFormSubmit}
          isSaving={createMutation.isPending || updateMutation.isPending}
          submitError={formError}
        />
      ) : (
        <Button size="sm" onClick={openCreateForm}>
          Add rule
        </Button>
      )}
    </div>
  )
}
