// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, useCallback, useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import { WorkingHoursSettings } from "@/components/calendar/WorkingHoursSettings"
import { SettingsSection } from "@/components/settings/SettingsSection"
import { ProfileSettings } from "@/components/settings/ProfileSettings"
import { ProviderTypeSettings } from "@/components/settings/ProviderTypeSettings"
import { SessionDefaults } from "@/components/settings/SessionDefaults"
import { IntegrationSettings } from "@/components/settings/IntegrationSettings"
import { TranscriptionSettings } from "@/components/settings/TranscriptionSettings"
import { AudioRetentionSettings } from "@/components/settings/AudioRetentionSettings"
import { PasskeySettings } from "@/components/settings/PasskeySettings"
import { AvailabilitySettings } from "@/components/settings/AvailabilitySettings"
import { BookingLinkSettings } from "@/components/settings/BookingLinkSettings"
import { Skeleton } from "@/components/ui/skeleton"
import { ThemeSwitcher } from "@/components/theme/ThemeSwitcher"
import { ThemeFlavorNote } from "@/components/theme/ThemeFlavorNote"
import { AlertCircle, Archive, Calendar, CalendarClock, Check, Clock, Link2, Mic, Palette, Settings2, ShieldCheck, User } from "lucide-react"
import { isEnabled } from "@/lib/featureFlags"
import { useConfig } from "@/lib/config"
import { getUserStatus, type UserPreferences } from "@/lib/api/users"

export default function SettingsPage() {
  const { passkeysEnabled, publicBookingEnabled } = useConfig()
  const { data: preferences, isLoading, error } = usePreferences()
  const saveMutation = useSavePreferences()
  const { data: userStatus } = useQuery({
    queryKey: ["user", "status"],
    queryFn: () => getUserStatus(),
    staleTime: 5 * 60 * 1000,
  })
  const practiceId = userStatus?.practice_id
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
        icon={Palette}
        title="Appearance"
        description="Pick the look of your workspace. Saved to your account."
      >
        <ThemeSwitcher />
        <ThemeFlavorNote />
      </SettingsSection>

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
        icon={User}
        title="Clinician type"
        description="Determines which note template and prompts Pablo uses for your visits."
      >
        <ProviderTypeSettings currentValue={userStatus?.provider_type ?? null} />
      </SettingsSection>

      <SettingsSection
        icon={Clock}
        title="Calendar display hours"
        description="The calendar highlights this window and scrolls to the start of your day."
      >
        <WorkingHoursSettings
          preferences={preferences}
          onSave={handleSave}
          isSaving={saveMutation.isPending}
        />
      </SettingsSection>

      <SettingsSection
        icon={CalendarClock}
        title="My availability"
        description="Rules that control when appointments can be booked, like blocked days or a max per day."
      >
        <AvailabilitySettings />
      </SettingsSection>

      {publicBookingEnabled && (
        <SettingsSection
          icon={Link2}
          title="Booking links"
          description="Public pages where clients pick a time. Each link books at a fixed length."
        >
          <BookingLinkSettings />
        </SettingsSection>
      )}

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

      {passkeysEnabled && (
        <SettingsSection
          icon={ShieldCheck}
          title="Passkeys"
          description="Phishing-resistant sign-in using your device's biometrics or security key."
        >
          <PasskeySettings />
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

      {isEnabled("audio_retention") && practiceId && (
        <SettingsSection
          icon={Archive}
          title="Audio Retention"
          description="How long session audio recordings are kept before nightly automatic deletion."
        >
          <AudioRetentionSettings practiceId={practiceId} />
        </SettingsSection>
      )}
    </div>
  )
}
