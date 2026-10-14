// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { usePreferences, useUpdatePreferences } from "@/hooks/usePreferences"
import { getUserStatus, type PreferenceChanges } from "@/lib/api/users"
import { useSettingsSaved } from "./SettingsSavedContext"

/**
 * The preference blob plus a save that flashes "Saved" in the page header.
 *
 * Every page that edits preferences calls this rather than wiring its own
 * mutation, so the confirmation appears in one place no matter which page
 * triggered it. React Query dedupes the underlying reads across pages.
 *
 * Pass only the keys being changed: `save({ theme: next })`. Spreading the
 * cached blob back in would put any key another save changed since it loaded
 * back the way it was.
 */
export function useSettingsPreferences() {
  const { data: preferences, isLoading, error } = usePreferences()
  const mutation = useUpdatePreferences()
  const { flashSaved } = useSettingsSaved()

  const save = useCallback(
    (changes: PreferenceChanges) => {
      mutation.mutate(changes, { onSuccess: () => flashSaved() })
    },
    [mutation, flashSaved]
  )

  return { preferences, isLoading, error, save, isSaving: mutation.isPending }
}

/** Identity facts the settings pages need: practice id, clinician type. */
export function useSettingsUserStatus() {
  return useQuery({
    queryKey: ["user", "status"],
    queryFn: () => getUserStatus(),
    staleTime: 5 * 60 * 1000,
  })
}
