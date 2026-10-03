// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { usePortalSettings } from "@/hooks/usePortalSettings"
import { PATIENT_PORTAL_SETTINGS_PATH } from "../paths"

/**
 * A portal address shows the practice's portal, so while the practice does not
 * offer one, even an active address has nothing to show. Said only when the
 * server says the portal is off: a deployment without the portal answers 404,
 * and then there is nothing to point at.
 */
export function PortalOffNote({ hasPortalHosts }: { hasPortalHosts: boolean }) {
  const { data } = usePortalSettings({ enabled: hasPortalHosts })
  const people = usePeopleTerm()
  if (!hasPortalHosts || data?.enabled !== false) return null
  return (
    <p className="mb-2 text-[12.5px] text-foreground" data-testid="domains-portal-off">
      Turn on the portal in{" "}
      <Link href={PATIENT_PORTAL_SETTINGS_PATH} className="underline">
        {people.One} portal
      </Link>{" "}
      before these addresses can show it.
    </p>
  )
}
