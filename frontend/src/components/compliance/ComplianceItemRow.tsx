// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText } from "lucide-react"
import { useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { formatCivilDate, LONG_DATE } from "@/lib/dates"
import { REPEAT_RULES, TASK_CATEGORIES, type Task } from "@/types/tasks"
import { type EnrichedItem, type HorizonId, categoryDot } from "./horizons"
import { formatDueLabel } from "./urgency"

export interface ComplianceItemRowProps {
  entry: EnrichedItem<Task>
  /**
   * Shown open, with its full notes and due date, and scrolled into view.
   * Set when a link elsewhere in the app pointed at this item.
   */
  expanded?: boolean
  onEdit: () => void
  /** Omitted for a task with no evidence to attach — any non-compliance task. */
  onOpenDocs?: () => void
  onComplete: () => void
  completing: boolean
  /** Name the client a linked task is about; off where the client is already the page. */
  showClient?: boolean
}

/** One task in the panel: a compliance reminder or any other task. */
export function ComplianceItemRow({
  entry,
  expanded = false,
  onEdit,
  onOpenDocs,
  onComplete,
  completing,
  showClient = true,
}: ComplianceItemRowProps) {
  const { item, days, horizon } = entry
  const client = showClient ? item.patient_name : null
  const dot = dotFor(item)
  const kind = kindLabel(item)
  const repeats = REPEAT_RULES.find((r) => r.id === item.repeat_rule)?.label
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
      data-category={item.category}
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
              {client && (
                <span className="font-medium text-neutral-700" data-testid="task-client-name">
                  {client} ·{" "}
                </span>
              )}
              {item.notes ?? kind}
            </p>
          )}
        </button>
        <span
          className={`hidden sm:inline-flex items-center text-[10.5px] font-medium px-2 py-0.5 rounded-full ${duePill}`}
        >
          {formatDueLabel(days)}
        </span>
        {onOpenDocs && (
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
        )}
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
          {client && <p className="text-neutral-800">{client}</p>}
          <p>{kind}</p>
          {item.due_date &&<p>Due {formatCivilDate(item.due_date, LONG_DATE, "en-US")}</p>}
          {repeats && <p>{repeats}</p>}
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

/** The row's second line when there are no notes: the template, or the category. */
function kindLabel(task: Task): string {
  if (task.category === "compliance") return labelForType(task.item_type)
  return TASK_CATEGORIES.find((c) => c.id === task.category)?.label ?? "Task"
}

const CATEGORY_DOTS: Record<string, string> = {
  practice: "bg-sky-400",
  other: "bg-slate-400",
}

function dotFor(task: Task): string {
  if (task.category === "compliance") return categoryDot(task.item_type)
  return CATEGORY_DOTS[task.category] ?? "bg-slate-400"
}

function labelForType(type: string): string {
  return type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}
