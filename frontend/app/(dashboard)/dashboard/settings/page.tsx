// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, useCallback, useRef } from "react"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import { WorkingHoursSettings } from "@/components/calendar/WorkingHoursSettings"
import { SettingsSection } from "@/components/settings/SettingsSection"
import { ProfileSettings } from "@/components/settings/ProfileSettings"
import { SessionDefaults } from "@/components/settings/SessionDefaults"
import { IntegrationSettings } from "@/components/settings/IntegrationSettings"
import { TranscriptionSettings } from "@/components/settings/TranscriptionSettings"
import { Skeleton } from "@/components/ui/skeleton"
import { AlertCircle, Calendar, Check, Clock, Mic, Settings2, User } from "lucide-react"
import { isEnabled } from "@/lib/featureFlags"
import type { UserPreferences } from "@/lib/api/users"

export default function SettingsPage() {
  const { data: preferences, isLoading, error } = usePreferences()
  const saveMutation = useSavePreferences()
  const [showSaved, setShowSaved] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout>>(null)

  const handleSave = useCallback(
    (prefs: UserPreferences) => {
      saveMutation.mutate(prefs, {
        onSuccess: () => {
          setShowSaved(true)
          if (timerRef.current) clearTimeout(timerRef.current)
          timerRef.current = setTimeout(() => setShowSaved(false), 2000)
        },
      })
    },
    [saveMutation]
  )

  if (isLoading) {
    return (
      <div className="space-y-6 max-w-2xl">
        <h1 className="text-3xl font-display font-semibold text-neutral-900">
          Settings
        </h1>
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  if (error || !preferences) {
    return (
      <div className="space-y-6 max-w-2xl">
        <h1 className="text-3xl font-display font-semibold text-neutral-900">
          Settings
        </h1>
        <div className="card p-8 text-center">
          <AlertCircle className="h-8 w-8 text-red-500 mx-auto mb-2" />
          <p className="text-neutral-600">Failed to load preferences.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-semibold text-neutral-900">
            Settings
          </h1>
          <p className="text-sm text-neutral-600 mt-1">Manage your preferences and defaults</p>
        </div>
        <div aria-live="polite" className="text-sm text-secondary-600 flex items-center gap-1.5">
          {showSaved && (
            <>
              <Check className="h-4 w-4" />
              Saved
            </>
          )}
        </div>
      </div>

      <SettingsSection
        icon={User}
        title="Profile"
        description="Your display name shown on notes and reports."
      >
        <ProfileSettings
          preferences={preferences}
          onSave={handleSave}
          isSaving={saveMutation.isPending}
        />
      </SettingsSection>

      <SettingsSection
        icon={Clock}
        title="Working Hours"
        description="The calendar highlights this window and scrolls to the start of your day."
      >
        <WorkingHoursSettings
          preferences={preferences}
          onSave={handleSave}
          isSaving={saveMutation.isPending}
        />
      </SettingsSection>

      {isEnabled("session_defaults") && (
        <SettingsSection
          icon={Settings2}
          title="Session Defaults"
          description="Default values pre-filled when creating new appointments."
        >
          <SessionDefaults
            preferences={preferences}
            onSave={handleSave}
            isSaving={saveMutation.isPending}
          />
        </SettingsSection>
      )}

      {isEnabled("calendar_integrations") && (
        <SettingsSection
          icon={Calendar}
          title="Calendar Integrations"
          description="Connect your EHR calendar to sync appointments into Pablo."
        >
          <IntegrationSettings />
        </SettingsSection>
      )}

      {isEnabled("transcription") && (
        <SettingsSection
          icon={Mic}
          title="Transcription"
          description="Configure automatic transcription behavior."
        >
          <TranscriptionSettings
            preferences={preferences}
            onSave={handleSave}
            isSaving={saveMutation.isPending}
          />
        </SettingsSection>
      )}
    </div>
  )
}
