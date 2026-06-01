// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionDetailHeader Component
 *
 * Displays session metadata in the detail page header:
 * - Patient name
 * - Session date (formatted; optionally editable inline)
 * - Session number
 * - Status badge with auto-polling
 *
 * The date can be made editable (e.g. to correct a date the note importer
 * read from a document). The edit is purely local UI state here; the parent
 * owns the mutation via ``onSessionDateChange``.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { format } from "date-fns"
import { ArrowLeft, Calendar, Check, Pencil, X } from "lucide-react"
import type { SessionStatus } from "@/types/sessions"
import { SessionStatusBadge } from "./SessionStatusBadge"

export interface SessionDetailHeaderProps {
  patientName: string
  sessionDate: string
  sessionNumber: number
  status: SessionStatus
  sessionId: string
  /** When true, the session date shows an inline edit affordance. */
  editableSessionDate?: boolean
  /** Called with the new ``datetime-local`` value when the clinician saves. */
  onSessionDateChange?: (value: string) => void
  savingSessionDate?: boolean
}

function toDateInputValue(iso: string): string {
  return format(new Date(iso), "yyyy-MM-dd'T'HH:mm")
}

export function SessionDetailHeader({
  patientName,
  sessionDate,
  sessionNumber,
  status,
  sessionId,
  editableSessionDate = false,
  onSessionDateChange,
  savingSessionDate = false,
}: SessionDetailHeaderProps) {
  const formattedDate = format(new Date(sessionDate), "MMMM d, yyyy")
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState("")

  const startEditing = () => {
    setValue(toDateInputValue(sessionDate))
    setEditing(true)
  }
  const save = () => {
    if (value) onSessionDateChange?.(value)
    setEditing(false)
  }

  return (
    <div className="space-y-4 border-b border-neutral-200 pb-6">
      <Link
        href="/dashboard/sessions"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-neutral-600 transition-colors hover:text-neutral-900"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Review
      </Link>
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-display font-bold text-neutral-900">
            {patientName}
          </h1>
          <div className="flex items-center gap-2 text-sm text-neutral-600">
            <Calendar className="h-4 w-4" />
            {editing ? (
              <span className="flex items-center gap-2">
                <input
                  type="datetime-local"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  className="rounded-md border border-neutral-300 px-2 py-1 text-sm"
                  aria-label="Session date and time"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={save}
                  disabled={savingSessionDate}
                  aria-label="Save date"
                  className="rounded-md p-1 text-primary-600 transition-colors hover:bg-primary-50 disabled:opacity-50"
                >
                  <Check className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setEditing(false)}
                  aria-label="Cancel editing date"
                  className="rounded-md p-1 text-neutral-500 transition-colors hover:bg-neutral-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </span>
            ) : (
              <>
                <span>{formattedDate}</span>
                {editableSessionDate && (
                  <button
                    type="button"
                    onClick={startEditing}
                    aria-label="Edit session date"
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-800"
                  >
                    <Pencil className="h-3 w-3" />
                    Edit
                  </button>
                )}
              </>
            )}
            <span className="text-neutral-400">•</span>
            <span>Session #{sessionNumber}</span>
          </div>
        </div>
        <SessionStatusBadge status={status} sessionId={sessionId} />
      </div>
    </div>
  )
}
