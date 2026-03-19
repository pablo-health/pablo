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

interface SessionDefaultsProps {
  preferences: UserPreferences
  onSave: (prefs: UserPreferences) => void
  isSaving: boolean
}

const SESSION_TYPES = [
  { value: "individual", label: "Individual" },
  { value: "couples", label: "Couples" },
  { value: "group", label: "Group" },
]

const DURATIONS = [
  { value: "25", label: "25 min" },
  { value: "50", label: "50 min" },
  { value: "60", label: "60 min" },
  { value: "75", label: "75 min" },
  { value: "90", label: "90 min" },
]

const PLATFORMS = [
  { value: "zoom", label: "Zoom" },
  { value: "google_meet", label: "Google Meet" },
  { value: "teams", label: "Microsoft Teams" },
  { value: "doxy", label: "Doxy.me" },
  { value: "other", label: "Other" },
]

export function SessionDefaults({ preferences, onSave, isSaving }: SessionDefaultsProps) {
  const handleChange = (field: keyof UserPreferences, value: string | number) => {
    onSave({ ...preferences, [field]: value })
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="grid gap-2">
        <Label htmlFor="default-session-type">Session Type</Label>
        <Select
          value={preferences.default_session_type}
          onValueChange={(v) => handleChange("default_session_type", v)}
          disabled={isSaving}
        >
          <SelectTrigger id="default-session-type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SESSION_TYPES.map((t) => (
              <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="default-duration">Duration</Label>
        <Select
          value={String(preferences.default_duration_minutes)}
          onValueChange={(v) => handleChange("default_duration_minutes", Number(v))}
          disabled={isSaving}
        >
          <SelectTrigger id="default-duration">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DURATIONS.map((d) => (
              <SelectItem key={d.value} value={d.value}>{d.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-2 sm:col-span-2 sm:max-w-[calc(50%-0.5rem)]">
        <Label htmlFor="default-platform">Video Platform</Label>
        <Select
          value={preferences.default_video_platform}
          onValueChange={(v) => handleChange("default_video_platform", v)}
          disabled={isSaving}
        >
          <SelectTrigger id="default-platform">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PLATFORMS.map((p) => (
              <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
