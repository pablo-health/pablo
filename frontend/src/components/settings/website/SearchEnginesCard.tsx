// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SavedIndicator, SettingsCard, SettingsRow, Toggle, useSavedFlash } from "../ui"

interface SearchEnginesCardProps {
  hidden: boolean
  canManage: boolean
  busy: boolean
  onChange: (hide: boolean, options: { onSuccess: () => void }) => void
}

const LABEL = "Hide from search engines"

/**
 * Whether the practice asks search engines not to list its website. On, every
 * answer on the website's hosts says `noindex, nofollow` and its robots.txt
 * disallows everything (src/lib/portal-host/practice-site.ts). Both are
 * requests that search engines honor, not a lock, so the copy says "asks" and
 * says the address still works; it never calls the website private.
 */
export function SearchEnginesCard({ hidden, canManage, busy, onChange }: SearchEnginesCardProps) {
  const { saved, flashSaved } = useSavedFlash()
  return (
    <SettingsCard title="Search engines" flush>
      <SettingsRow
        label={LABEL}
        description="Asks Google and other search engines not to list your website. Anyone with the address can still visit it."
      >
        <SavedIndicator saved={saved} />
        <Toggle
          label={LABEL}
          checked={hidden}
          disabled={!canManage || busy}
          onChange={(next) => onChange(next, { onSuccess: flashSaved })}
        />
      </SettingsRow>
    </SettingsCard>
  )
}
