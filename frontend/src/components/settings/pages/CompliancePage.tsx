// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Practice > Compliance. Licenses, attestations and renewals.
 *
 * The same panel the dashboard shows beside the week, given a page of its own
 * so it has an address: a due date elsewhere in the app links here with
 * `?item=<id>` and the page opens on that item. Both surfaces read and write
 * the same items, so an item added on either shows on the other.
 */

import { useSearchParams } from "next/navigation"
import { CompliancePanel } from "@/components/compliance/CompliancePanel"

/** The query parameter a link uses to open the page on one item. */
export const COMPLIANCE_ITEM_PARAM = "item"

/** Where a link to one compliance item points. */
export function complianceItemHref(itemId: string): string {
  return `/dashboard/settings/compliance?${COMPLIANCE_ITEM_PARAM}=${encodeURIComponent(itemId)}`
}

export function CompliancePage() {
  const focusItemId = useSearchParams()?.get(COMPLIANCE_ITEM_PARAM) ?? null
  return <CompliancePanel focusItemId={focusItemId} variant="page" />
}
