// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { UserPreferences } from "@/lib/api/users"

interface TranscriptionSettingsProps {
  preferences: UserPreferences
  onSave: (prefs: UserPreferences) => void
  isSaving: boolean
}

const QUALITY_PRESETS = [
  { value: "fast", label: "Fast" },
  { value: "balanced", label: "Balanced" },
  { value: "accurate", label: "Accurate" },
]

export function TranscriptionSettings({ preferences, onSave, isSaving }: TranscriptionSettingsProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Checkbox
          id="auto-transcribe"
          checked={preferences.auto_transcribe}
          onCheckedChange={(checked) =>
            onSave({ ...preferences, auto_transcribe: checked === true })
          }
          disabled={isSaving}
        />
        <Label htmlFor="auto-transcribe" className="cursor-pointer">
          Automatically transcribe uploaded recordings
        </Label>
      </div>

      <div className="grid gap-2 max-w-xs">
        <Label htmlFor="quality-preset">Quality Preset</Label>
        <Select
          value={preferences.quality_preset}
          onValueChange={(v) => onSave({ ...preferences, quality_preset: v })}
          disabled={isSaving}
        >
          <SelectTrigger id="quality-preset">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {QUALITY_PRESETS.map((q) => (
              <SelectItem key={q.value} value={q.value}>{q.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
