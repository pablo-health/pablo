// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { WaitlistList } from "@/components/waitlist/WaitlistList"
import { SettingsCard } from "../ui"

/**
 * Practice > Waitlist.
 *
 * The clinician's own list, top first. A client is added from their page;
 * here the order is set and each entry paused, switched or removed.
 */
export function WaitlistPage() {
  return (
    <SettingsCard>
      <WaitlistList />
    </SettingsCard>
  )
}
