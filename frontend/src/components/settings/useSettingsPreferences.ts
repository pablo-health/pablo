// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import { getUserStatus, type UserPreferences } from "@/lib/api/users"
import { useSettingsSaved } from "./SettingsSavedContext"

/**
 * The preference blob plus a save that flashes "Saved" in the page header.
 *
 * Every page that edits preferences calls this rather than wiring its own
 * mutation, so the confirmation appears in one place no matter which page
 * triggered it. React Query dedupes the underlying reads across pages.
 *
 * Note the save is a FULL REPLACE of the blob, which is why callers spread the
 * current value: `save({ ...preferences, theme: next })`.
 */
export function useSettingsPreferences() {
  const { data: preferences, isLoading, error } = usePreferences()
  const mutation = useSavePreferences()
  const { flashSaved } = useSettingsSaved()

  const save = useCallback(
    (next: UserPreferences) => {
      mutation.mutate(next, { onSuccess: () => flashSaved() })
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
