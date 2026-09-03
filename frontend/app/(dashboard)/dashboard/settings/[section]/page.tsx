// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { notFound, useParams } from "next/navigation"
import { useFeature } from "@/lib/featureGates"
import { findSettingsItem } from "@/components/settings/registry"

/**
 * One settings page, addressed by its registry id.
 *
 * The route enforces the gate itself rather than trusting that the nav hid the
 * link: a hidden link is not access control, and people bookmark and share
 * these URLs. Same posture as the calendar setup route.
 */
export default function SettingsSectionPage() {
  const params = useParams<{ section: string }>()
  const section = typeof params?.section === "string" ? params.section : ""
  const item = findSettingsItem(section)
  const enabled = useFeature(item?.feature)

  if (!item || !enabled) {
    notFound()
  }

  const Page = item.page
  return <Page />
}
