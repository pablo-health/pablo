// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { useMemo, useState } from "react"
import { PanelLoadError } from "@/components/support/PanelLoadError"
import { Button } from "@/components/ui/button"
import {
  useComplianceItems,
  useComplianceTemplates,
  useCompleteComplianceItem,
} from "@/hooks/useCompliance"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"
import { ComplianceItemRow } from "./ComplianceItemRow"
import { DocumentsDialog } from "./DocumentsDialog"
import { HorizonStrip } from "./HorizonStrip"
import { ReminderComposer } from "./ReminderComposer"
import { type HorizonId, enrichItems, sortByDueDate } from "./horizons"
import { SupervisionHeroCard } from "./SupervisionHeroCard"

type Selection = HorizonId | "urgent" | "all"

export interface CompliancePanelProps {
  /**
   * An item to open on arrival: its horizon is selected, its row expanded and
   * scrolled to. Other screens link here with `?item=<id>`; an id that matches
   * nothing is ignored.
   */
  focusItemId?: string | null
  /**
   * "page" when the panel is the whole of a settings page, whose header
   * already names it; the panel then drops its own title.
   */
  variant?: "panel" | "page"
}

/** The horizon tab that shows a given horizon's items. */
function selectionFor(horizon: HorizonId): Selection {
  if (horizon === "overdue" || horizon === "week" || horizon === "month") return "urgent"
  if (horizon === "informational") return "all"
  return horizon
}

export function CompliancePanel({ focusItemId = null, variant = "panel" }: CompliancePanelProps = {}) {
  const {
    data: itemsData,
    isLoading: itemsLoading,
    isError: itemsFailed,
    refetch: refetchItems,
  } = useComplianceItems()
  const items = useMemo(() => itemsData ?? [], [itemsData])
  const { data: templates = [] } = useComplianceTemplates()
  const completeItem = useCompleteComplianceItem()
  const [composerOpen, setComposerOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<ComplianceItem | null>(null)
  const [docsItem, setDocsItem] = useState<ComplianceItem | null>(null)
  // Null until the person picks a tab, so the focused item's horizon can be
  // the starting tab once the items arrive, without an effect to sync it.
  const [chosen, setChosen] = useState<Selection | null>(null)

  const templateByType = useMemo(() => {
    const m = new Map<string, ComplianceTemplate>()
    for (const t of templates) m.set(t.item_type, t)
    return m
  }, [templates])

  const enriched = useMemo(
    () => enrichItems(items, templateByType),
    [items, templateByType],
  )

  const focused = useMemo(
    () => (focusItemId ? enriched.find((e) => e.item.id === focusItemId) : undefined),
    [enriched, focusItemId],
  )
  const selection: Selection = chosen ?? (focused ? selectionFor(focused.horizon) : "urgent")

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
    let list
    if (selection === "urgent") list = urgent
    else if (selection === "all") {
      list = [...enriched]
        .filter((e) => e.horizon !== "informational")
        .sort(sortByDueDate)
    } else {
      list = enriched
        .filter((e) => e.horizon === selection)
        .sort(sortByDueDate)
    }
    // An item with no date sits in no horizon; a link to it still shows it.
    if (focused && !list.includes(focused)) list = [...list, focused]
    return list
  }, [selection, enriched, urgent, focused])

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
        <PanelHeader onAdd={openComposerForAdd} hasAny={false} variant={variant} />
        <p className="text-sm text-neutral-500 py-6 text-center">Loading…</p>
      </div>
    )
  }

  // Without this a failed load showed the first-time "set up your reminders"
  // screen to someone who may already have them.
  if (itemsFailed && !itemsData) {
    return (
      <div className="card">
        <PanelHeader onAdd={openComposerForAdd} hasAny={false} variant={variant} />
        <PanelLoadError
          message="Your reminders didn’t load."
          onRetry={() => void refetchItems()}
        />
      </div>
    )
  }

  if (!hasAny) {
    return (
      <div className="card">
        <PanelHeader onAdd={openComposerForAdd} hasAny={false} variant={variant} />
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
  const showAllClear = selection === "urgent" && noUrgent && !focused

  return (
    <div className="card">
      <PanelHeader onAdd={openComposerForAdd} hasAny variant={variant} />
      <SupervisionHeroCard />
      <HorizonStrip counts={counts} selected={selection} onSelect={setChosen} />

      {showAllClear ? (
        <AllClear />
      ) : visible.length === 0 ? (
        <EmptyBucket />
      ) : (
        <ul className="space-y-1.5" role="list">
          {visible.map((entry) => (
            <ComplianceItemRow
              key={entry.item.id}
              entry={entry}
              expanded={entry === focused}
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
  variant,
}: {
  onAdd: () => void
  hasAny: boolean
  variant: "panel" | "page"
}) {
  if (variant === "page") {
    if (!hasAny) return null
    return (
      <div className="flex justify-end mb-4">
        <Button variant="outline" size="sm" onClick={onAdd}>
          Add reminder
        </Button>
      </div>
    )
  }
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
