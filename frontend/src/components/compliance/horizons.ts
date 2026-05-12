// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"
import { daysUntil, urgencyFor } from "./urgency"

export type HorizonId =
  | "overdue"
  | "week"
  | "month"
  | "quarter"
  | "beyond"
  | "informational"

export interface HorizonDef {
  id: HorizonId
  label: string
  short: string
  hint: string
}

export const HORIZONS: HorizonDef[] = [
  { id: "overdue", label: "Overdue", short: "Past due", hint: "Address now" },
  { id: "week", label: "This week", short: "≤ 7 days", hint: "Due imminently" },
  { id: "month", label: "This month", short: "≤ 30 days", hint: "Plan ahead" },
  { id: "quarter", label: "Next 90", short: "31–90 days", hint: "On the horizon" },
  { id: "beyond", label: "Beyond", short: "90+ days", hint: "Far future" },
]

export interface EnrichedItem {
  item: ComplianceItem
  template: ComplianceTemplate | undefined
  days: number | null
  horizon: HorizonId
  isUrgent: boolean
}

export function horizonFor(days: number | null): HorizonId {
  if (days === null) return "informational"
  if (days < 0) return "overdue"
  if (days <= 7) return "week"
  if (days <= 30) return "month"
  if (days <= 90) return "quarter"
  return "beyond"
}

export function enrichItems(
  items: ComplianceItem[],
  templateByType: Map<string, ComplianceTemplate>,
): EnrichedItem[] {
  return items.map((item) => {
    const template = templateByType.get(item.item_type)
    const days = daysUntil(item.due_date)
    const u = template ? urgencyFor(item, template) : "informational"
    return {
      item,
      template,
      days,
      horizon: horizonFor(days),
      isUrgent: u === "overdue" || u === "due-soon",
    }
  })
}

export function sortByDueDate(a: EnrichedItem, b: EnrichedItem): number {
  return (
    (a.days ?? Number.POSITIVE_INFINITY) -
    (b.days ?? Number.POSITIVE_INFINITY)
  )
}

/**
 * Category color for a compliance item type. Grouped by domain so the
 * dashboard reads at a glance — credentials warm, training cool, etc.
 * Unknown types deterministically pick from a small palette via a hash.
 */
const CATEGORY_PALETTE: Record<string, string> = {
  license: "bg-amber-400",
  malpractice: "bg-rose-400",
  liability: "bg-rose-400",
  caqh: "bg-amber-400",
  npi: "bg-amber-400",
  insurance: "bg-rose-400",
  hipaa_training: "bg-emerald-400",
  training: "bg-emerald-400",
  attestation: "bg-emerald-400",
  audit_log_review: "bg-primary-400",
  compliance_audit: "bg-primary-400",
  vendor_inventory: "bg-primary-400",
  vendor_verification: "bg-primary-400",
  legal_review: "bg-primary-400",
  backup_verification: "bg-sky-400",
  dr_test: "bg-sky-400",
  asset_inventory_review: "bg-sky-400",
  vuln_scan: "bg-slate-500",
  pentest: "bg-slate-500",
}

const FALLBACK_PALETTE = [
  "bg-amber-400",
  "bg-emerald-400",
  "bg-sky-400",
  "bg-rose-400",
  "bg-slate-500",
  "bg-primary-400",
]

export function categoryDot(itemType: string): string {
  if (CATEGORY_PALETTE[itemType]) return CATEGORY_PALETTE[itemType]
  let h = 0
  for (let i = 0; i < itemType.length; i++) h = (h * 31 + itemType.charCodeAt(i)) | 0
  return FALLBACK_PALETTE[Math.abs(h) % FALLBACK_PALETTE.length]
}
