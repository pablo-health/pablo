// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Calendar, ChevronDown, ChevronUp, UserPlus } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SegmentedControl, SettingsBadge, Toggle } from "@/components/settings/ui"
import { SchedulingTypeExtras } from "@/components/settings/settingsSlots.extensions"
import { cn } from "@/lib/utils"
import type {
  AppointmentAudience,
  AppointmentTypeResponse,
  UpdateAppointmentTypeRequest,
} from "@/types/scheduling"

const AUDIENCE_LABEL: Record<AppointmentAudience, string> = {
  new: "New patients",
  existing: "Existing patients",
  both: "Anyone",
}

const AUDIENCE_OPTIONS: { value: AppointmentAudience; label: string }[] = [
  { value: "new", label: "New patients" },
  { value: "existing", label: "Existing patients" },
  { value: "both", label: "Anyone" },
]

/** Radix `Select.Item` rejects an empty-string value, so "defer to the
 * practice default" needs a sentinel distinct from every real hour count. */
const PRACTICE_DEFAULT_NOTICE = "practice-default"

const NOTICE_OPTIONS: { value: string; label: string }[] = [
  { value: "2", label: "2 hours" },
  { value: "12", label: "12 hours" },
  { value: "24", label: "A day" },
  { value: "48", label: "2 days" },
  { value: "72", label: "3 days" },
  { value: "168", label: "A week" },
]

const EARLIEST_OPTIONS: { value: string; label: string }[] = [
  { value: "0", label: "Same day" },
  { value: "1", label: "Next day" },
  { value: "2", label: "2 business days out" },
  { value: "3", label: "3 business days out" },
  { value: "5", label: "About a week out" },
]

const HORIZON_OPTIONS: { value: string; label: string }[] = [
  { value: "5|business", label: "5 business days" },
  { value: "10|business", label: "10 business days" },
  { value: "14|days", label: "2 weeks" },
  { value: "21|days", label: "3 weeks" },
  { value: "30|days", label: "30 days" },
  { value: "60|days", label: "60 days" },
]

/** "24" -> "1 day", "48" -> "2 days", "2" -> "2 h". Mirrors the design's `hrs`. */
function formatHours(hours: number): string {
  if (hours >= 24 && hours % 24 === 0) {
    const days = hours / 24
    return `${days} day${days === 1 ? "" : "s"}`
  }
  return `${hours} h`
}

function earliestLabel(days: number): string {
  const match = EARLIEST_OPTIONS.find((o) => Number(o.value) === days)
  return match ? match.label.toLowerCase() : `${days} business days out`
}

function horizonLabel(horizon: number, unit: string): string {
  const match = HORIZON_OPTIONS.find((o) => o.value === `${horizon}|${unit}`)
  if (match) return match.label.toLowerCase()
  return `${horizon} ${unit === "business" ? "business days" : "days"}`
}

/** Whole-dollar fee input <-> stored cents. `null` (unset) is distinct from 0 (free). */
function feeInputValue(cents: number | null): string {
  return cents == null ? "" : String(cents / 100)
}

function parseFeeInput(raw: string): number | null {
  if (raw.trim() === "") return null
  const dollars = Number(raw)
  if (!Number.isFinite(dollars) || dollars < 0) return null
  return Math.round(dollars * 100)
}

export function AppointmentTypeRow({
  appointmentType,
  open,
  onToggle,
  onChange,
  onDelete,
  selfBookOn,
  defaultNoticeHours,
}: {
  appointmentType: AppointmentTypeResponse
  open: boolean
  onToggle: () => void
  onChange: (patch: UpdateAppointmentTypeRequest) => void
  onDelete: () => void
  selfBookOn: boolean
  defaultNoticeHours: number
}) {
  const [nameDraft, setNameDraft] = useState(appointmentType.name)
  const isNewPatientType = appointmentType.audience === "new"
  const notice = appointmentType.min_notice_hours ?? defaultNoticeHours
  const fee = appointmentType.default_fee_cents

  return (
    <li className="block p-0">
      <div className="flex items-center gap-3.5 py-3">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-3 border-0 bg-transparent p-0 text-left [font:inherit]"
        >
          <div
            className={cn(
              "grid h-8 w-8 shrink-0 place-items-center rounded-[9px]",
              isNewPatientType ? "bg-accent-300/25 text-accent-500" : "bg-primary-500/20 text-primary-700"
            )}
          >
            {isNewPatientType ? <UserPlus className="h-4 w-4" aria-hidden="true" /> : <Calendar className="h-4 w-4" aria-hidden="true" />}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <span>{appointmentType.name}</span>
              <span className="font-medium text-muted-foreground">
                {appointmentType.duration_minutes} min
                {fee != null && <> &middot; {fee === 0 ? "Free" : `$${fee / 100}`}</>}
              </span>
              <SettingsBadge tone={appointmentType.audience === "new" ? "sky" : appointmentType.audience === "both" ? "mute" : "honey"}>
                {AUDIENCE_LABEL[appointmentType.audience]}
              </SettingsBadge>
            </div>
            <small className="mt-0.5 block text-[12.5px] text-muted-foreground">
              Offered from {earliestLabel(appointmentType.earliest_offer_business_days)} up to{" "}
              {horizonLabel(appointmentType.horizon, appointmentType.horizon_unit)} out, needs {formatHours(notice)} warning
              {appointmentType.min_notice_hours == null ? " (default)" : ""}
            </small>
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-[18px]">
          <SchedulingTypeExtras appointmentType={appointmentType} onChange={onChange} />
          <label className="grid justify-items-center gap-1 text-[10.5px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
            Self-book
            <Toggle
              checked={appointmentType.self_bookable && selfBookOn}
              onChange={(v) => selfBookOn && onChange({ self_bookable: v })}
              disabled={!selfBookOn}
              label={`${appointmentType.name} self-book`}
            />
          </label>
          <Button type="button" variant="ghost" size="icon-sm" onClick={onToggle} aria-label={open ? "Collapse" : "Expand"}>
            {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      {open && (
        <div className="ml-11 mb-3 grid gap-3.5 rounded-xl border border-border bg-foreground/[0.04] p-[18px]">
          <div className="grid grid-cols-[1.4fr_1fr_1fr] gap-3">
            <label className="grid gap-1 text-[12.5px] font-semibold text-foreground">
              Name
              <Input
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={() => nameDraft.trim() && onChange({ name: nameDraft })}
              />
            </label>
            <label className="grid gap-1 text-[12.5px] font-semibold text-foreground">
              Length
              <span className="flex items-center gap-1.5">
                <Input
                  type="number"
                  step={5}
                  min={5}
                  max={480}
                  value={appointmentType.duration_minutes}
                  onChange={(e) => onChange({ duration_minutes: Number(e.target.value) })}
                />
                <span className="font-normal text-muted-foreground">min</span>
              </span>
            </label>
            <label className="grid gap-1 text-[12.5px] font-semibold text-foreground">
              Fee
              <span className="flex items-center gap-1.5">
                <span className="font-normal text-muted-foreground">$</span>
                <Input
                  type="number"
                  min={0}
                  value={feeInputValue(fee)}
                  onChange={(e) => onChange({ default_fee_cents: parseFeeInput(e.target.value) })}
                />
              </span>
            </label>
          </div>

          <div className="grid gap-1">
            <span className="text-[12.5px] font-semibold text-foreground">Who is this for?</span>
            <SegmentedControl
              label="Who is this for?"
              value={appointmentType.audience}
              onChange={(value) => onChange({ audience: value })}
              options={AUDIENCE_OPTIONS}
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <label className="grid gap-1 text-[12.5px] font-semibold text-foreground">
              How much warning do you need?
              <Select
                value={
                  appointmentType.min_notice_hours == null
                    ? PRACTICE_DEFAULT_NOTICE
                    : String(appointmentType.min_notice_hours)
                }
                onValueChange={(value) =>
                  onChange({ min_notice_hours: value === PRACTICE_DEFAULT_NOTICE ? null : Number(value) })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={PRACTICE_DEFAULT_NOTICE}>
                    Practice default ({formatHours(defaultNoticeHours)})
                  </SelectItem>
                  {NOTICE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="grid gap-1 text-[12.5px] font-semibold text-foreground">
              Earliest Pablo may offer
              <Select
                value={String(appointmentType.earliest_offer_business_days)}
                onValueChange={(value) => onChange({ earliest_offer_business_days: Number(value) })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EARLIEST_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="grid gap-1 text-[12.5px] font-semibold text-foreground">
              How far ahead
              <Select
                value={`${appointmentType.horizon}|${appointmentType.horizon_unit}`}
                onValueChange={(value) => {
                  const [horizon, unit] = value.split("|")
                  onChange({ horizon: Number(horizon), horizon_unit: unit as "business" | "days" })
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {HORIZON_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          </div>

          <div className="flex items-center justify-between gap-3">
            <span className="text-[12.5px] text-muted-foreground">
              Which days count comes from{" "}
              <Link href="/dashboard/settings/availability" className="underline">
                Availability
              </Link>
              . This never overrides it.
            </span>
            <Button type="button" variant="destructive" size="sm" onClick={onDelete}>
              Delete type
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}
