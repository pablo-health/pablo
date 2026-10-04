// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Checkbox } from "@/components/ui/checkbox"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { usePreferences } from "@/hooks/usePreferences"
import { useSettingsPreferences, useSettingsUserStatus } from "./useSettingsPreferences"

/**
 * Whether a session from a followed Google Calendar or a calendar feed books
 * on its own when its title is the full name of exactly one client.
 *
 * The clinician's own choice when they have made one; otherwise the server's
 * default, which user status reports already resolved, so the default lives
 * in one place (the backend). Undefined until either has loaded.
 */
export function useBooksSessionsNamedInTitle(): boolean | undefined {
  const { data: preferences } = usePreferences()
  const { data: status } = useSettingsUserStatus()
  return preferences?.book_sessions_named_in_title ?? status?.books_sessions_named_in_title
}

/**
 * The choice itself. Off, such a session is asked about with the client
 * already chosen; answers given before book either way. The rule for which
 * titles count is the backend's (`outside_sessions`), so the label keeps to
 * the plain version.
 */
export function NameBookingSetting() {
  const people = usePeopleTerm()
  const { preferences, save, isSaving } = useSettingsPreferences()
  const books = useBooksSessionsNamedInTitle()
  if (!preferences || books === undefined) return null
  const label = `Book sessions whose title has a ${people.one}’s full name`

  return (
    <label className="flex cursor-pointer items-start gap-3">
      <Checkbox
        className="mt-0.5"
        checked={books}
        disabled={isSaving}
        aria-label={label}
        onCheckedChange={(checked) =>
          save({ ...preferences, book_sessions_named_in_title: checked === true })
        }
      />
      <span className="space-y-0.5">
        <span className="block text-sm text-neutral-900">{label}</span>
        <span className="block text-xs text-muted-foreground">
          Otherwise Pablo asks first, with the {people.one} already chosen.
        </span>
      </span>
    </label>
  )
}
