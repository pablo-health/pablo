// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText } from "lucide-react"
import Image from "next/image"
import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  useComplianceItems,
  useComplianceTemplates,
  useCompleteComplianceItem,
} from "@/hooks/useCompliance"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"
import { DocumentsDialog } from "./DocumentsDialog"
import { HorizonStrip } from "./HorizonStrip"
import { ReminderComposer } from "./ReminderComposer"
import {
  type EnrichedItem,
  type HorizonId,
  categoryDot,
  enrichItems,
  sortByDueDate,
} from "./horizons"
import { formatDueLabel } from "./urgency"
import { SupervisionHeroCard } from "./SupervisionHeroCard"

type Selection = HorizonId | "urgent" | "all"

export function CompliancePanel() {
  const { data: items = [], isLoading: itemsLoading } = useComplianceItems()
  const { data: templates = [] } = useComplianceTemplates()
  const completeItem = useCompleteComplianceItem()
  const [composerOpen, setComposerOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<ComplianceItem | null>(null)
  const [docsItem, setDocsItem] = useState<ComplianceItem | null>(null)
  const [selection, setSelection] = useState<Selection>("urgent")

  const templateByType = useMemo(() => {
    const m = new Map<string, ComplianceTemplate>()
    for (const t of templates) m.set(t.item_type, t)
    return m
  }, [templates])

  const enriched = useMemo(
    () => enrichItems(items, templateByType),
    [items, templateByType],
  )

  const counts = useMemo<Record<HorizonId, number>>(() => {
    const c: Record<HorizonId, number> = {
      overdue: 0,
      week: 0,
      month: 0,
      quarter: 0,
      beyond: 0,
      informational: 0,
    }
    for (const e of enriched) c[e.horizon]++
    return c
  }, [enriched])

  // Must match HorizonStrip's urgentCount (overdue + week + month), not isUrgent.
  const urgent = useMemo(
    () =>
      enriched
        .filter((e) =>
          e.horizon === "overdue" ||
          e.horizon === "week" ||
          e.horizon === "month",
        )
        .sort(sortByDueDate),
    [enriched],
  )

  const visible = useMemo(() => {
    if (selection === "urgent") return urgent
    if (selection === "all") {
      return [...enriched]
        .filter((e) => e.horizon !== "informational")
        .sort(sortByDueDate)
    }
    return enriched
      .filter((e) => e.horizon === selection)
      .sort(sortByDueDate)
  }, [selection, enriched, urgent])

  function openComposerForAdd() {
    setEditingItem(null)
    setComposerOpen(true)
  }

  function openComposerForEdit(item: ComplianceItem) {
    setEditingItem(item)
    setComposerOpen(true)
  }

  const hasAny = items.length > 0

  if (itemsLoading) {
    return (
      <div className="card">
        <PanelHeader onAdd={openComposerForAdd} hasAny={false} />
        <p className="text-sm text-neutral-500 py-6 text-center">Loading…</p>
      </div>
    )
  }

  if (!hasAny) {
    return (
      <div className="card">
        <PanelHeader onAdd={openComposerForAdd} hasAny={false} />
        <SupervisionHeroCard />
        <EmptyState onStart={openComposerForAdd} />
        <ReminderComposer
          open={composerOpen}
          onOpenChange={setComposerOpen}
          templates={templates}
          items={items}
          initialItem={editingItem}
        />
      </div>
    )
  }

  const noUrgent = urgent.length === 0
  const showAllClear = selection === "urgent" && noUrgent

  return (
    <div className="card">
      <PanelHeader onAdd={openComposerForAdd} hasAny />
      <SupervisionHeroCard />
      <HorizonStrip counts={counts} selected={selection} onSelect={setSelection} />

      {showAllClear ? (
        <AllClear />
      ) : visible.length === 0 ? (
        <EmptyBucket />
      ) : (
        <ul className="space-y-1.5" role="list">
          {visible.map((entry) => (
            <ItemRow
              key={entry.item.id}
              entry={entry}
              onEdit={() => openComposerForEdit(entry.item)}
              onOpenDocs={() => setDocsItem(entry.item)}
              onComplete={() => completeItem.mutate(entry.item.id)}
              completing={completeItem.isPending}
            />
          ))}
        </ul>
      )}

      <ReminderComposer
        open={composerOpen}
        onOpenChange={setComposerOpen}
        templates={templates}
        items={items}
        initialItem={editingItem}
      />

      {docsItem && (
        <DocumentsDialog
          open={docsItem !== null}
          onOpenChange={(open) => !open && setDocsItem(null)}
          item={docsItem}
        />
      )}
    </div>
  )
}

function PanelHeader({
  onAdd,
  hasAny,
}: {
  onAdd: () => void
  hasAny: boolean
}) {
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <h2 className="text-xl font-display font-semibold text-neutral-900">
          Compliance
        </h2>
        <p className="text-sm text-neutral-600 mt-1">
          License renewal, insurance, attestation, and training reminders.
        </p>
      </div>
      {hasAny && (
        <Button variant="outline" size="sm" onClick={onAdd}>
          Add reminder
        </Button>
      )}
    </div>
  )
}

function ItemRow({
  entry,
  onEdit,
  onOpenDocs,
  onComplete,
  completing,
}: {
  entry: EnrichedItem
  onEdit: () => void
  onOpenDocs: () => void
  onComplete: () => void
  completing: boolean
}) {
  const { item, days, horizon } = entry
  const dot = categoryDot(item.item_type)
  const duePill = pillFor(horizon)

  // Row body opens the edit dialog; "Mark done" stopPropagation so the primary
  // "I just did the thing" action never pops a dialog by accident.
  return (
    <li className="group flex items-center gap-3 rounded-lg border border-neutral-200/70 bg-white/60 px-3 py-2 hover:bg-white hover:border-neutral-300 transition-colors">
      <span
        className={`h-2.5 w-2.5 rounded-full shrink-0 ${dot}`}
        aria-hidden
      />
      <button
        type="button"
        onClick={onEdit}
        className="min-w-0 flex-1 text-left"
        aria-label={`Edit ${item.label}`}
      >
        <p className="text-sm font-medium text-neutral-900 truncate">
          {item.label}
        </p>
        <p className="text-xs text-neutral-500 mt-0.5 truncate">
          {item.notes ?? labelForType(item.item_type)}
        </p>
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
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

const REMINDER_MONTHS = [
  "jan", "feb", "mar", "apr", "may", "jun",
  "jul", "aug", "sep", "oct", "nov", "dec",
] as const

function EmptyState({ onStart }: { onStart: () => void }) {
  // Pablo holds up a desk calendar showing the current month.
  const month = useMemo(() => REMINDER_MONTHS[new Date().getMonth()], [])
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image
        src={`/pablo-reminders-${month}.webp`}
        alt="Pablo bear, your documentation companion"
        width={96}
        height={96}
        priority
      />
      <p className="font-display text-lg text-neutral-900 mt-3">
        Let&apos;s set up your reminders
      </p>
      <p className="text-sm text-neutral-600 mt-1 max-w-sm">
        Pablo will nudge you before your license, insurance, and attestations
        come due. Pick the ones you want — skip the rest.
      </p>
      <Button className="mt-4" onClick={onStart}>
        Add reminder
      </Button>
    </div>
  )
}

function AllClear() {
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image
        src="/pablo-tie.webp"
        alt="Pablo bear"
        width={64}
        height={64}
      />
      <p className="text-sm text-neutral-700 mt-3">
        You&apos;re all caught up. Pablo&apos;s got it from here.
      </p>
    </div>
  )
}

function EmptyBucket() {
  return (
    <p className="text-sm text-neutral-500 py-6 text-center">
      Nothing in this horizon. Try another bucket above.
    </p>
  )
}
