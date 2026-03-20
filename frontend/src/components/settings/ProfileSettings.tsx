// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import type { UserPreferences } from "@/lib/api/users"

interface ProfileSettingsProps {
  preferences: UserPreferences
  onSave: (prefs: UserPreferences) => void
  isSaving: boolean
}

export function ProfileSettings({ preferences, onSave, isSaving }: ProfileSettingsProps) {
  const [displayName, setDisplayName] = useState(preferences.therapist_display_name ?? "")

  const handleSave = () => {
    onSave({ ...preferences, therapist_display_name: displayName || null })
  }

  const isDirty = displayName !== (preferences.therapist_display_name ?? "")

  return (
    <div className="space-y-3">
      <div className="grid gap-2 max-w-sm">
        <Label htmlFor="display-name">Display Name</Label>
        <Input
          id="display-name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Dr. Jane Smith"
        />
      </div>
      {isDirty && (
        <Button size="sm" onClick={handleSave} disabled={isSaving}>
          {isSaving ? "Saving..." : "Save"}
        </Button>
      )}
    </div>
  )
}
