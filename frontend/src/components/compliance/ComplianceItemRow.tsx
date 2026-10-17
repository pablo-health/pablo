// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText } from "lucide-react"
import { useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { type EnrichedItem, type HorizonId, categoryDot } from "./horizons"
import { formatDueLabel } from "./urgency"

export interface ComplianceItemRowProps {
  entry: EnrichedItem
  /**
   * Shown open, with its full notes and due date, and scrolled into view.
   * Set when a link elsewhere in the app pointed at this item.
   */
  expanded?: boolean
  onEdit: () => void
  onOpenDocs: () => void
  onComplete: () => void
  completing: boolean
}

export function ComplianceItemRow({
  entry,
  expanded = false,
  onEdit,
  onOpenDocs,
  onComplete,
  completing,
}: ComplianceItemRowProps) {
  const { item, days, horizon } = entry
  const dot = categoryDot(item.item_type)
  const duePill = pillFor(horizon)
  const rowRef = useRef<HTMLLIElement>(null)

  useEffect(() => {
    // jsdom has no layout, so scrollIntoView is absent there.
    if (expanded) rowRef.current?.scrollIntoView?.({ block: "center" })
  }, [expanded])

  // Row body opens the edit dialog; "Mark done" stopPropagation so the primary
  // "I just did the thing" action never pops a dialog by accident.
  return (
    <li
      ref={rowRef}
      data-testid={`compliance-item-${item.id}`}
      data-expanded={expanded ? "true" : undefined}
      aria-current={expanded ? "true" : undefined}
      className={`group rounded-lg border px-3 py-2 transition-colors ${
        expanded
          ? "border-primary-300 bg-white ring-2 ring-primary-200"
          : "border-neutral-200/70 bg-white/60 hover:bg-white hover:border-neutral-300"
      }`}
    >
      <div className="flex items-center gap-3">
        <span className={`h-2.5 w-2.5 rounded-full shrink-0 ${dot}`} aria-hidden />
        <button
          type="button"
          onClick={onEdit}
          className="min-w-0 flex-1 text-left"
          aria-label={`Edit ${item.label}`}
        >
          <p className={`text-sm font-medium text-neutral-900 ${expanded ? "" : "truncate"}`}>
            {item.label}
          </p>
          {!expanded && (
            <p className="text-xs text-neutral-500 mt-0.5 truncate">
              {item.notes ?? labelForType(item.item_type)}
            </p>
          )}
        </button>
        <span
          className={`hidden sm:inline-flex items-center text-[10.5px] font-medium px-2 py-0.5 rounded-full ${duePill}`}
        >
          {formatDueLabel(days)}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={(e) => {
            e.stopPropagation()
            onOpenDocs()
          }}
          className="opacity-70 group-hover:opacity-100 transition-opacity"
          aria-label={`Documents for ${item.label}`}
        >
          <FileText className="h-4 w-4" aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={(e) => {
            e.stopPropagation()
            onComplete()
          }}
          disabled={completing}
          className="opacity-70 group-hover:opacity-100 transition-opacity"
        >
          Mark done
        </Button>
      </div>
      {expanded && (
        <div className="mt-2 pl-5 space-y-1 text-xs text-neutral-600" data-testid="compliance-item-details">
          <p>{labelForType(item.item_type)}</p>
          {item.due_date && <p>Due {formatDueDate(item.due_date)}</p>}
          {item.notes && <p className="whitespace-pre-wrap">{item.notes}</p>}
          <Button variant="outline" size="sm" className="mt-1" onClick={onEdit}>
            Edit
          </Button>
        </div>
      )}
    </li>
  )
}

function pillFor(horizon: HorizonId): string {
  switch (horizon) {
    case "overdue":
      return "bg-rose-100 text-rose-800"
    case "week":
      return "bg-amber-100 text-amber-800"
    case "month":
      return "bg-amber-50 text-amber-700"
    case "quarter":
      return "bg-emerald-50 text-emerald-700"
    case "beyond":
      return "bg-neutral-100 text-neutral-600"
    default:
      return "bg-neutral-100 text-neutral-500"
  }
}

function labelForType(type: string): string {
  return type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

/** A stored `YYYY-MM-DD` as a calendar date, without a timezone shift. */
function formatDueDate(isoDate: string): string {
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number)
  if (!y || !m || !d) return isoDate
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  })
}
