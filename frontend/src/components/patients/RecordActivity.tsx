// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQuery } from "@tanstack/react-query"
import { History } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { describeActivity, formatAuditTimestamp } from "@/components/settings/auditLogDisplay"
import { useRecordActivity } from "@/hooks/useRecordActivity"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { getUserStatus } from "@/lib/api/users"

/**
 * "Record activity" in the chart header: who acted on this record, and when.
 *
 * The practice owner's alone — the server refuses anyone else, and the link
 * is not shown to them. A side panel rather than another chart tab: it is a
 * review the owner opens now and then, not part of working the chart.
 */
export function RecordActivity({ patientId }: { patientId: string }) {
  const { data: status } = useQuery({
    queryKey: ["user", "status"],
    queryFn: () => getUserStatus(),
    staleTime: 5 * 60 * 1000,
  })
  const people = usePeopleTerm()

  if (status?.can_view_record_activity !== true) return null

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="link" size="sm">
          <History className="h-4 w-4" />
          Record activity
        </Button>
      </DialogTrigger>
      <DialogContent className="top-0 right-0 left-auto h-dvh max-h-dvh w-full max-w-md translate-x-0 translate-y-0 rounded-none p-0 sm:max-w-md">
        <DialogHeader className="text-left">
          <DialogTitle className="font-display text-lg font-bold text-neutral-900">
            Record activity
          </DialogTitle>
          <DialogDescription className="text-sm text-neutral-500">
            Who acted on this {people.one}&apos;s record, and when.
          </DialogDescription>
        </DialogHeader>
        <ActivityList patientId={patientId} />
      </DialogContent>
    </Dialog>
  )
}

function ActivityList({ patientId }: { patientId: string }) {
  const people = usePeopleTerm()
  const { entries, isLoading, isError, refetch, hasMore, loadMore, isLoadingMore } =
    useRecordActivity(patientId)

  if (isError) {
    return (
      <div className="text-sm text-neutral-600">
        <p>Activity could not be loaded.</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  if (isLoading) return <p className="text-sm text-neutral-500">Loading…</p>
  if (entries.length === 0) return <p className="text-sm text-neutral-500">Nothing recorded yet.</p>

  return (
    <div>
      <ol className="divide-y divide-neutral-200" aria-label="Record activity">
        {entries.map((item) => (
          <li key={item.id} className="py-3">
            <p className="text-sm font-medium text-neutral-900">{describeActivity(item, people)}</p>
            <p className="text-xs text-neutral-500">{formatAuditTimestamp(item.timestamp)}</p>
          </li>
        ))}
      </ol>
      {hasMore && (
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={() => loadMore()}
          disabled={isLoadingMore}
        >
          {isLoadingMore ? "Loading…" : "Load older"}
        </Button>
      )}
    </div>
  )
}
