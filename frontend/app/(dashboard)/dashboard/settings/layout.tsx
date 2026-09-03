// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { ReactNode } from "react"
import { SettingsNav } from "@/components/settings/SettingsNav"
import { SettingsSavedProvider } from "@/components/settings/SettingsSavedContext"
import { SavedIndicator, useSavedFlash } from "@/components/settings/ui"
import { SettingsPageHeader } from "@/components/settings/SettingsPageHeader"

/**
 * The settings surface: a section nav on the left, one page on the right.
 *
 * The "Saved" flash lives up here because most settings save on change with no
 * button, so the confirmation has to be somewhere stable rather than next to
 * whichever control was touched.
 */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  const { saved, flashSaved } = useSavedFlash()

  return (
    <SettingsSavedProvider saved={saved} flashSaved={flashSaved}>
      <div className="-m-4 grid min-h-[calc(100vh-4rem)] grid-cols-1 md:-m-6 md:grid-cols-[252px_minmax(0,1fr)]">
        <div className="hidden md:block">
          <SettingsNav />
        </div>
        <main className="min-w-0 px-6 pb-14 pt-7 md:px-10">
          <div className="max-w-[720px]">
            <div className="mb-[22px] flex items-start justify-between gap-5">
              <SettingsPageHeader />
              <div className="pt-[34px]">
                <SavedIndicator saved={saved} />
              </div>
            </div>
            {children}
          </div>
        </main>
      </div>
    </SettingsSavedProvider>
  )
}
