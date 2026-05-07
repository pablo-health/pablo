// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  useComplianceItems,
  useComplianceTemplates,
  useCompleteComplianceItem,
} from "@/hooks/useCompliance"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"
import { ComplianceWizard } from "./ComplianceWizard"
import { daysUntil, formatDueLabel, urgencyFor } from "./urgency"

const URGENCY_STYLES: Record<string, string> = {
  overdue: "bg-red-50 text-red-700 border-red-200",
  "due-soon": "bg-primary-50 text-primary-700 border-primary-200",
  upcoming: "bg-neutral-50 text-neutral-600 border-neutral-200",
  informational: "bg-neutral-50 text-neutral-500 border-neutral-200",
}

export function CompliancePanel() {
  const { data: items = [], isLoading: itemsLoading } = useComplianceItems()
  const { data: templates = [] } = useComplianceTemplates()
  const completeItem = useCompleteComplianceItem()
  const [wizardOpen, setWizardOpen] = useState(false)

  const templateByType = useMemo(() => {
    const m = new Map<string, ComplianceTemplate>()
    for (const t of templates) m.set(t.item_type, t)
    return m
  }, [templates])

  // Surface anything urgent or overdue, sorted soonest-first.
  const visible = useMemo(() => {
    const enriched = items.map((i) => ({
      item: i,
      template: templateByType.get(i.item_type),
      days: daysUntil(i.due_date),
    }))
    return enriched
      .filter(({ item, template }) => {
        if (!template) return false
        const u = urgencyFor(item, template)
        return u === "overdue" || u === "due-soon"
      })
      .sort(
        (a, b) => (a.days ?? Number.POSITIVE_INFINITY) - (b.days ?? Number.POSITIVE_INFINITY),
      )
  }, [items, templateByType])

  const hasAny = items.length > 0

  return (
    <div className="card">
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
          <Button variant="outline" size="sm" onClick={() => setWizardOpen(true)}>
            Manage
          </Button>
        )}
      </div>

      {itemsLoading ? (
        <p className="text-sm text-neutral-500 py-6 text-center">Loading…</p>
      ) : !hasAny ? (
        <EmptyState onStart={() => setWizardOpen(true)} />
      ) : visible.length === 0 ? (
        <AllClear />
      ) : (
        <ul className="space-y-2">
          {visible.map(({ item, template, days }) => {
            const u = urgencyFor(item, template!)
            return (
              <li
                key={item.id}
                className={`flex items-center justify-between rounded-md border px-3 py-2 ${URGENCY_STYLES[u]}`}
              >
                <div className="min-w-0">
                  <p className="font-medium truncate">{item.label}</p>
                  <p className="text-xs">{formatDueLabel(days)}</p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => completeItem.mutate(item.id)}
                  disabled={completeItem.isPending}
                >
                  Mark done
                </Button>
              </li>
            )
          })}
        </ul>
      )}

      <ComplianceWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </div>
  )
}

function EmptyState({ onStart }: { onStart: () => void }) {
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image
        src="/pablo-tie.webp"
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
        come due. Takes about two minutes.
      </p>
      <Button className="mt-4" onClick={onStart}>
        Start setup
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
