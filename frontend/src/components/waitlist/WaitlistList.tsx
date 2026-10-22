// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * WaitlistList
 *
 * The clinician's waitlist, top first. Each entry can move up or down, be
 * paused or resumed, have automatic offers switched on or off, or come off
 * the list.
 *
 * A move sends the whole list in its new order. If the server's list has
 * changed since this one was read (an entry added from a client page in
 * another tab, say), it refuses with 409; the list is read again and the
 * clinician tries once more. Nothing moves on screen until the server agrees.
 *
 * `no_auto_offer` is stored the other way round from how it is shown: the
 * switch reads "Offer times automatically", on unless the clinician turned it
 * off for this entry.
 */

"use client"

import Link from "next/link"
import { ArrowDown, ArrowUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/Toast"
import { Toggle } from "@/components/settings/ui"
import {
  useRemoveWaitlistEntry,
  useReorderWaitlist,
  useUpdateWaitlistEntry,
  useWaitlist,
} from "@/hooks/useWaitlist"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { ApiError } from "@/lib/api/client"
import type { WaitlistEntry } from "@/types/waitlist"
import {
  AUTO_OFFER,
  LOAD_FAILED,
  ORDER_CHANGED,
  PAUSE,
  PAUSED,
  REMOVE,
  RESUME,
  SAVE_FAILED,
  emptyState,
  entrySummary,
} from "./waitlistCopy"

/** The ids in their new order, with the one at `index` moved by `step`. */
export function movedOrder(entries: WaitlistEntry[], index: number, step: -1 | 1): string[] {
  const ids = entries.map((e) => e.id)
  const [moved] = ids.splice(index, 1)
  ids.splice(index + step, 0, moved)
  return ids
}

interface RowProps {
  entry: WaitlistEntry
  name: string
  first: boolean
  last: boolean
  busy: boolean
  readOnly: boolean
  onMove: (step: -1 | 1) => void
}

function Row({ entry, name, first, last, busy, readOnly, onMove }: RowProps) {
  const update = useUpdateWaitlistEntry()
  const remove = useRemoveWaitlistEntry()
  const { showToast } = useToast()
  const pending = busy || update.isPending || remove.isPending

  async function change(data: Partial<Pick<WaitlistEntry, "paused" | "no_auto_offer">>) {
    try {
      await update.mutateAsync({ entryId: entry.id, data })
    } catch {
      showToast(SAVE_FAILED, "error")
    }
  }

  async function takeOff() {
    try {
      await remove.mutateAsync(entry.id)
    } catch {
      showToast(SAVE_FAILED, "error")
    }
  }

  return (
    <li
      data-testid="waitlist-entry"
      data-entry-id={entry.id}
      data-patient-id={entry.patient_id}
      data-paused={entry.paused}
      className="flex flex-col gap-2 border-t border-border py-3 first:border-t-0 first:pt-1 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 items-start gap-3">
        <span
          data-testid="waitlist-entry-position"
          className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-foreground/[0.08] text-xs font-semibold text-muted-foreground"
        >
          {entry.position}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/dashboard/patients/${entry.patient_id}`}
              className="text-sm font-semibold text-foreground hover:underline"
              data-testid="waitlist-entry-name"
            >
              {name}
            </Link>
            {entry.paused && (
              <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs font-medium text-neutral-700">
                {PAUSED}
              </span>
            )}
          </div>
          <div
            data-testid="waitlist-entry-summary"
            className="mt-0.5 text-[12.5px] text-muted-foreground"
          >
            {entrySummary(entry)}
          </div>
        </div>
      </div>

      {!readOnly && (
        <div className="flex shrink-0 flex-wrap items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Move ${name} up`}
            disabled={first || pending}
            onClick={() => onMove(-1)}
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Move ${name} down`}
            disabled={last || pending}
            onClick={() => onMove(1)}
          >
            <ArrowDown className="h-4 w-4" />
          </Button>
          <span className="flex items-center gap-2 px-2 text-xs text-muted-foreground">
            <Toggle
              checked={!entry.no_auto_offer}
              label={`${AUTO_OFFER} for ${name}`}
              disabled={pending}
              onChange={(on) => void change({ no_auto_offer: !on })}
            />
            <span aria-hidden="true">{AUTO_OFFER}</span>
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => void change({ paused: !entry.paused })}
          >
            {entry.paused ? RESUME : PAUSE}
          </Button>
          <Button size="sm" variant="ghost" disabled={pending} onClick={() => void takeOff()}>
            {REMOVE}
          </Button>
        </div>
      )}
    </li>
  )
}

export function WaitlistList() {
  const { data, isPending, error, refetch } = useWaitlist()
  const reorder = useReorderWaitlist()
  const people = usePeopleTerm()
  const { showToast } = useToast()
  const { readOnly } = useReadOnlyMode()

  if (isPending) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }

  if (error || !data) {
    return <p className="text-sm text-red-500">{LOAD_FAILED}</p>
  }

  const entries = data.entries

  if (entries.length === 0) {
    return (
      <p data-testid="waitlist-empty" className="py-4 text-center text-sm text-muted-foreground">
        {emptyState(people)}
      </p>
    )
  }

  async function move(index: number, step: -1 | 1) {
    try {
      await reorder.mutateAsync(movedOrder(entries, index, step))
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        await refetch()
        showToast(ORDER_CHANGED, "error")
        return
      }
      showToast(SAVE_FAILED, "error")
    }
  }

  return (
    <ol aria-label="Waitlist" data-testid="waitlist-list">
      {entries.map((entry, index) => (
        <Row
          key={entry.id}
          entry={entry}
          name={entry.patient_name ?? people.One}
          first={index === 0}
          last={index === entries.length - 1}
          busy={reorder.isPending}
          readOnly={readOnly}
          onMove={(step) => void move(index, step)}
        />
      ))}
    </ol>
  )
}
