// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { UserPreferences } from "@/lib/api/users"

interface WorkingHoursSettingsProps {
  preferences: UserPreferences
  onSave: (prefs: UserPreferences) => void
  isSaving: boolean
}

export function formatHour(hour: number): string {
  if (hour === 0 || hour === 24) return "12:00 AM"
  if (hour === 12) return "12:00 PM"
  if (hour < 12) return `${hour}:00 AM`
  return `${hour - 12}:00 PM`
}

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, i) => i)

export function WorkingHoursSettings({
  preferences,
  onSave,
  isSaving,
}: WorkingHoursSettingsProps) {
  const handleStartChange = (value: string) => {
    const start = Number(value)
    if (start < preferences.working_hours_end) {
      onSave({ ...preferences, working_hours_start: start })
    }
  }

  const handleEndChange = (value: string) => {
    const end = Number(value)
    if (end > preferences.working_hours_start) {
      onSave({ ...preferences, working_hours_end: end })
    }
  }

  const TIMELINE_START = 6
  const TIMELINE_END = 22
  const totalSlots = TIMELINE_END - TIMELINE_START
  const leftPercent = ((Math.max(preferences.working_hours_start, TIMELINE_START) - TIMELINE_START) / totalSlots) * 100
  const rightPercent = ((Math.min(preferences.working_hours_end, TIMELINE_END) - TIMELINE_START) / totalSlots) * 100

  return (
    <div className="space-y-4">
      <h3 className="sr-only">Working Hours</h3>
      <p className="text-sm text-neutral-600">
        Set your typical working hours. The calendar will highlight this
        window and scroll to the start of your day. You can still scroll
        to see times outside working hours.
      </p>
      <div className="flex items-end gap-4">
        <div className="grid gap-2">
          <Label htmlFor="working-hours-start">Start</Label>
          <Select
            value={String(preferences.working_hours_start)}
            onValueChange={handleStartChange}
            disabled={isSaving}
          >
            <SelectTrigger id="working-hours-start" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOUR_OPTIONS.filter((h) => h < preferences.working_hours_end).map(
                (h) => (
                  <SelectItem key={h} value={String(h)}>
                    {formatHour(h)}
                  </SelectItem>
                )
              )}
            </SelectContent>
          </Select>
        </div>
        <span className="pb-2 text-neutral-400">to</span>
        <div className="grid gap-2">
          <Label htmlFor="working-hours-end">End</Label>
          <Select
            value={String(preferences.working_hours_end)}
            onValueChange={handleEndChange}
            disabled={isSaving}
          >
            <SelectTrigger id="working-hours-end" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOUR_OPTIONS.filter(
                (h) => h > preferences.working_hours_start
              ).map((h) => (
                <SelectItem key={h} value={String(h)}>
                  {formatHour(h)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Decorative timeline bar */}
      <div aria-hidden="true" className="relative h-3 rounded-full bg-neutral-100 overflow-hidden">
        <div
          className="absolute top-0 bottom-0 rounded-full bg-primary-200"
          style={{ left: `${leftPercent}%`, width: `${rightPercent - leftPercent}%` }}
        />
      </div>
      <div aria-hidden="true" className="flex justify-between text-[10px] text-neutral-400 px-0.5">
        <span>6 AM</span>
        <span>12 PM</span>
        <span>10 PM</span>
      </div>
    </div>
  )
}
