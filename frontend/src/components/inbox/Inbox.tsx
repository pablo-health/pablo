// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Inbox: everything waiting on the clinician, one list.
 *
 * Filters narrow it by kind, and Open / Done switches between what still
 * needs doing and what was handled, which can be put back. An item opens
 * beside the list (or in place of it, on a narrow screen) through its kind's
 * renderer. The filter, view and open item live in the URL, so a link can
 * land on one message.
 *
 * The open item is kept as it was when opened. Replying to a message
 * resolves it, which takes it off the Open list at the next read — but the
 * clinician is still looking at it, and what the reply did (the question
 * about earlier messages) is shown there.
 */

"use client"

import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import { Inbox as InboxIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useDismissInboxItem,
  useInboxItems,
  useRestoreInboxItems,
  useSnoozeInboxItem,
} from "@/hooks/useInbox"
import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import { usePortalSettings } from "@/hooks/usePortalSettings"
import type { InboxItem, InboxView } from "@/lib/api/inbox"
import { WHEN } from "./ItemPanel"
import { inboxFilters, renderInboxItem } from "./itemRenderers"
import { portalOffNotice } from "./portalOff"

const DISPOSITION_LABELS: Record<string, string> = {
  replied: "Replied",
  handled: "Marked handled",
  dismissed: "Dismissed",
}

/** Tomorrow at 8 in the morning, the browser's time: the one snooze offered. */
export function tomorrowMorning(now: Date = new Date()): Date {
  const next = new Date(now)
  next.setDate(next.getDate() + 1)
  next.setHours(8, 0, 0, 0)
  return next
}

function sameItem(a: InboxItem, b: InboxItem): boolean {
  return a.kind === b.kind && a.source_id === b.source_id
}

export function Inbox() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const filter = inboxFilters.find((f) => f.id === params.get("filter")) ?? inboxFilters[0]
  const view: InboxView = params.get("view") === "done" ? "done" : "open"
  const itemParam = params.get("item")

  const { data, isLoading, isError, refetch } = useInboxItems(view, filter.kinds)
  const [selected, setSelected] = useState<InboxItem | null>(null)
  // The item named in the URL is opened once, when the list holding it
  // arrives — not again every time the list refetches or the URL catches up
  // with a close.
  const [openedFromUrl, setOpenedFromUrl] = useState<string | null>(null)
  if (itemParam && openedFromUrl !== itemParam) {
    const match = data?.data.find((item) => item.source_id === itemParam)
    if (match) {
      setOpenedFromUrl(itemParam)
      setSelected(match)
    }
  }

  function navigate(next: { filter?: string; view?: InboxView; item?: string | null }) {
    const query = new URLSearchParams()
    const nextFilter = next.filter ?? filter.id
    const nextView = next.view ?? view
    const nextItem = next.item === undefined ? itemParam : next.item
    if (nextFilter !== "all") query.set("filter", nextFilter)
    if (nextView !== "open") query.set("view", nextView)
    if (nextItem) query.set("item", nextItem)
    const qs = query.toString()
    router.replace(qs ? `${pathname}?${qs}` : pathname)
  }

  function open(item: InboxItem | null) {
    // On close, the URL still names the old item until the router catches up.
    setOpenedFromUrl(item?.source_id ?? itemParam)
    setSelected(item)
    navigate({ item: item?.source_id ?? null })
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Show">
          {inboxFilters.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={option.id === filter.id}
              onClick={() => {
                setSelected(null)
                navigate({ filter: option.id, item: null })
              }}
              className={`rounded-full px-3 py-1 text-sm font-medium ${
                option.id === filter.id ? "bg-primary-50 text-primary-700" : "text-neutral-600 hover:bg-neutral-100"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="flex gap-3 text-sm" aria-label="Open or done">
          {(["open", "done"] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => {
                setSelected(null)
                navigate({ view: option, item: null })
              }}
              className={view === option ? "font-semibold text-neutral-900" : "text-neutral-500"}
            >
              {option === "open" ? "Open" : "Done"}
            </button>
          ))}
        </div>
      </div>

      {filter.kinds.includes("portal_message") && <PortalOffBanner />}

      <div className="grid min-h-[70vh] overflow-hidden rounded-2xl border border-neutral-200 bg-card md:grid-cols-[380px_1fr]">
        <section className={`min-h-0 border-neutral-200 md:border-r ${selected ? "hidden md:block" : ""}`}>
          <ItemList
            items={data?.data}
            isLoading={isLoading}
            isError={isError}
            onRetry={() => void refetch()}
            selected={selected}
            onOpen={open}
          />
        </section>
        <section className={`min-h-0 ${selected ? "" : "hidden md:block"}`}>
          {selected ? (
            <OpenItem key={`${selected.kind}:${selected.source_id}`} item={selected} view={view} onClose={() => open(null)} />
          ) : (
            <div className="flex h-full items-center justify-center p-8 text-sm text-neutral-500">
              Choose an item to open it.
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function PortalOffBanner() {
  const { data: portal } = usePortalSettings()
  const notice = portalOffNotice(portal)
  if (!notice) return null
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"
      data-testid="messages-portal-off"
    >
      <p className="text-sm text-neutral-800">{notice}</p>
      <Link href="/dashboard/settings/portal" className="text-sm font-medium underline">
        Client portal settings
      </Link>
    </div>
  )
}

interface ItemListProps {
  items: InboxItem[] | undefined
  isLoading: boolean
  isError: boolean
  onRetry: () => void
  selected: InboxItem | null
  onOpen: (item: InboxItem) => void
}

function ItemList({ items, isLoading, isError, onRetry, selected, onOpen }: ItemListProps) {
  const timeZone = useUserTimeZone()
  if (isLoading) {
    return (
      <div className="space-y-2 p-4" data-testid="inbox-loading">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    )
  }
  if (isError) {
    return (
      <div className="p-6 text-center" role="alert">
        <p className="text-sm text-neutral-700">The Inbox didn&rsquo;t load.</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      </div>
    )
  }
  if (!items || items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-8 text-center text-sm text-neutral-500" data-testid="inbox-empty">
        <InboxIcon className="h-6 w-6" aria-hidden="true" />
        Nothing needs you right now.
      </div>
    )
  }
  return (
    <ul data-testid="inbox-list">
      {items.map((item) => {
        const active = selected !== null && sameItem(selected, item)
        return (
          <li key={`${item.kind}:${item.source_id}`}>
            <button
              type="button"
              onClick={() => onOpen(item)}
              aria-current={active ? "true" : undefined}
              data-testid="inbox-row"
              data-kind={item.kind}
              data-source-id={item.source_id}
              className={`flex w-full items-start gap-3 border-b border-neutral-100 px-4 py-3 text-left ${
                active ? "bg-primary-50" : "hover:bg-neutral-50"
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-neutral-900">
                    {item.patient_name ?? item.title}
                  </span>
                  <span className="shrink-0 text-xs text-neutral-500">
                    {formatInUserTimeZone(item.occurred_at, timeZone, WHEN)}
                  </span>
                </div>
                {item.patient_name && <p className="truncate text-sm text-neutral-700">{item.title}</p>}
                {item.detail && <p className="truncate text-sm text-neutral-500">{item.detail}</p>}
                {item.disposition && (
                  <p className="text-xs text-neutral-500" data-testid="inbox-row-disposition">
                    {dispositionLabel(item, timeZone)}
                  </p>
                )}
              </div>
              {item.severity === "urgent" && (
                <span className="shrink-0 rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">
                  Urgent
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function dispositionLabel(item: InboxItem, timeZone: string): string {
  if (item.disposition === "snoozed" && item.snoozed_until) {
    return `Snoozed until ${formatInUserTimeZone(item.snoozed_until, timeZone, WHEN)}`
  }
  return DISPOSITION_LABELS[item.disposition ?? ""] ?? "Done"
}

function OpenItem({ item, view, onClose }: { item: InboxItem; view: InboxView; onClose: () => void }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <ItemActions item={item} view={view} onDone={onClose} />
      <div className="min-h-0 flex-1">
        {renderInboxItem({ item, onClose })}
      </div>
    </div>
  )
}

/**
 * The Inbox's own verbs. Open items can be dismissed ("handled elsewhere") or
 * snoozed; done ones can be put back. Handling the item itself happens in the
 * item, where it already happens.
 */
function ItemActions({ item, view, onDone }: { item: InboxItem; view: InboxView; onDone: () => void }) {
  const dismiss = useDismissInboxItem()
  const snooze = useSnoozeInboxItem()
  const restore = useRestoreInboxItems()
  const ref = { kind: item.kind, sourceId: item.source_id }
  const busy = dismiss.isPending || snooze.isPending || restore.isPending

  return (
    <div className="flex flex-wrap justify-end gap-2 border-b border-neutral-200 px-4 py-2" data-testid="inbox-item-actions">
      {view === "open" ? (
        <>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => snooze.mutate({ ...ref, until: tomorrowMorning() }, { onSuccess: onDone })}>
            Snooze until tomorrow
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => dismiss.mutate(ref, { onSuccess: onDone })}>
            Dismiss
          </Button>
        </>
      ) : (
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => restore.mutate([ref], { onSuccess: onDone })}>
          Restore
        </Button>
      )}
    </div>
  )
}
