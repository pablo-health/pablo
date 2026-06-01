// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionDetailHeader Component
 *
 * Displays session metadata in the detail page header:
 * - Patient name
 * - Session date (formatted)
 * - Session number
 * - Status badge with auto-polling
 */

import Link from "next/link"
import { format } from "date-fns"
import { ArrowLeft, Calendar } from "lucide-react"
import type { SessionStatus } from "@/types/sessions"
import { SessionStatusBadge } from "./SessionStatusBadge"

export interface SessionDetailHeaderProps {
  patientName: string
  sessionDate: string
  sessionNumber: number
  status: SessionStatus
  sessionId: string
}

export function SessionDetailHeader({
  patientName,
  sessionDate,
  sessionNumber,
  status,
  sessionId,
}: SessionDetailHeaderProps) {
  const formattedDate = format(new Date(sessionDate), "MMMM d, yyyy")

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
            <span>{formattedDate}</span>
            <span className="text-neutral-400">•</span>
            <span>Session #{sessionNumber}</span>
          </div>
        </div>
        <SessionStatusBadge status={status} sessionId={sessionId} />
      </div>
    </div>
  )
}
