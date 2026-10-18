// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { notFound, useParams } from "next/navigation"
import { useSettledFeature } from "@/lib/featureGates"
import { findSettingsItem } from "@/components/settings/registry"

/**
 * One settings page, addressed by its registry id.
 *
 * The route enforces the gate itself rather than trusting that the nav hid the
 * link: a hidden link is not access control, and people bookmark and share
 * these URLs. Same posture as the calendar setup route.
 *
 * It waits for the final answer before refusing. A page the account has
 * been given, opened directly (a bookmark, a shared link, a reload), would
 * otherwise 404 on the deployment's provisional answer before the
 * account's own answer arrived, and a 404 does not come back.
 */
export default function SettingsSectionPage() {
  const params = useParams<{ section: string }>()
  const section = typeof params?.section === "string" ? params.section : ""
  const item = findSettingsItem(section)
  const enabled = useSettledFeature(item?.feature)

  if (!item || enabled === false) {
    notFound()
  }
  if (enabled === null) return null

  const Page = item.page
  return <Page />
}
