// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The prescriber's refill queue: what patients have asked for from the
 * portal, oldest first, and the last answers given.
 *
 * A decision is two clicks on purpose — pick the answer, then confirm it —
 * because it is recorded once and the patient sees it. The optional note is
 * the practice's own; the patient surface never carries it.
 *
 * Nothing here sends a prescription anywhere. "Sent to pharmacy" records
 * that the prescriber sent it.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { Pill } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useToast } from "@/components/ui/Toast"
import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import { isAlreadyAnswered, useDecideRefill, useRefillQueue } from "@/hooks/useRefillRequests"
import type {
  ClinicianRefillRequest,
  RefillDecision,
  RefillStatus,
} from "@/lib/api/refillRequests"

const DECISIONS: { status: RefillDecision; label: string }[] = [
  { status: "approved", label: "Sent to pharmacy" },
  { status: "needs_visit", label: "Needs a visit" },
  { status: "declined", label: "Not refilled" },
]

export const DECISION_LABELS: Record<RefillStatus, string> = {
  requested: "Waiting",
  approved: "Sent to pharmacy",
  needs_visit: "Needs a visit",
  declined: "Not refilled",
}

const DATE_FORMAT: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
}

export function RefillQueue() {
  return (
    <div className="space-y-6">
      <PendingRequests />
      <RecentDecisions />
    </div>
  )
}

function PendingRequests() {
  const { data, isLoading, isError, refetch } = useRefillQueue("pending")

  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="refill-queue-loading">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="card text-center py-8" role="alert" data-testid="refill-queue-error">
        <p className="text-sm text-neutral-700">Refill requests didn&rsquo;t load.</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const requests = data?.data ?? []

  if (requests.length === 0) {
    return (
      <div className="card text-center py-12" data-testid="refill-queue-empty">
        <Pill className="mx-auto h-8 w-8 text-neutral-300" />
        <p className="mt-3 text-sm font-medium text-neutral-900">No refill requests waiting</p>
        <p className="mt-1 text-sm text-neutral-500">
          Requests your patients send from the portal show up here.
        </p>
      </div>
    )
  }

  return (
    <ul className="card space-y-3" data-testid="refill-queue">
      {requests.map((request) => (
        <PendingRow key={request.id} request={request} />
      ))}
    </ul>
  )
}

function PendingRow({ request }: { request: ClinicianRefillRequest }) {
  const timeZone = useUserTimeZone()
  const decide = useDecideRefill()
  const { showToast } = useToast()
  const [choice, setChoice] = useState<RefillDecision | null>(null)
  const [note, setNote] = useState("")

  const confirm = async () => {
    if (choice === null) return
    try {
      await decide.mutateAsync({
        requestId: request.id,
        status: choice,
        prescriberNote: note.trim() || null,
      })
    } catch (error) {
      showToast(
        isAlreadyAnswered(error)
          ? "Someone at your practice already answered this request."
          : "That didn't save. Try again.",
        "error",
      )
    }
  }

  return (
    <li
      data-testid={`refill-row-${request.id}`}
      className="rounded-md border border-neutral-200 p-4 space-y-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <Link
            href={`/dashboard/patients/${request.patient_id}`}
            className="text-sm font-medium text-neutral-900 hover:underline"
          >
            {request.patient_name ?? "Patient"}
          </Link>
          <p className="text-sm text-neutral-900" data-testid="refill-medication">
            {request.medication_text}
          </p>
          {request.pharmacy_text && (
            <p className="text-xs text-neutral-600">Pharmacy: {request.pharmacy_text}</p>
          )}
          {request.patient_note && (
            <p className="text-xs text-neutral-600 whitespace-pre-wrap break-words">
              &ldquo;{request.patient_note}&rdquo;
            </p>
          )}
        </div>
        <span className="shrink-0 text-xs text-neutral-500">
          Asked {formatInUserTimeZone(request.created_at, timeZone, DATE_FORMAT)}
        </span>
      </div>

      {choice === null ? (
        <div className="flex flex-wrap gap-2">
          {DECISIONS.map(({ status, label }) => (
            <Button
              key={status}
              size="sm"
              variant={status === "approved" ? "default" : "outline"}
              data-testid={`refill-decide-${status}`}
              onClick={() => setChoice(status)}
            >
              {label}
            </Button>
          ))}
        </div>
      ) : (
        <div className="space-y-2" data-testid="refill-confirm">
          <Label htmlFor={`refill-note-${request.id}`}>
            Private note (optional)
          </Label>
          <Textarea
            id={`refill-note-${request.id}`}
            data-testid="refill-note"
            value={note}
            maxLength={2000}
            onChange={(event) => setNote(event.target.value)}
            rows={2}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              data-testid="refill-confirm-submit"
              disabled={decide.isPending}
              onClick={() => void confirm()}
            >
              {decide.isPending ? "Saving…" : `Confirm: ${DECISION_LABELS[choice]}`}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={decide.isPending}
              onClick={() => {
                setChoice(null)
                setNote("")
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

function RecentDecisions() {
  const { data } = useRefillQueue("recent")
  const timeZone = useUserTimeZone()
  const decided = data?.data ?? []

  if (decided.length === 0) return null

  return (
    <details className="card" data-testid="refill-recent">
      <summary className="cursor-pointer text-sm font-medium text-neutral-900">
        Recent decisions
      </summary>
      <ul className="mt-3 space-y-2">
        {decided.map((request) => (
          <li
            key={request.id}
            data-testid={`refill-recent-${request.id}`}
            className="flex flex-wrap items-center justify-between gap-2 text-sm"
          >
            <span className="min-w-0 truncate">
              <span className="font-medium text-neutral-900">{request.patient_name ?? "Patient"}</span>
              <span className="text-neutral-600"> · {request.medication_text}</span>
            </span>
            <span className="shrink-0 text-xs text-neutral-600">
              {DECISION_LABELS[request.status]}
              {request.decided_at &&
                ` · ${formatInUserTimeZone(request.decided_at, timeZone, DATE_FORMAT)}`}
            </span>
          </li>
        ))}
      </ul>
    </details>
  )
}
