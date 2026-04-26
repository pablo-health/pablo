// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionsTable Component
 *
 * Display list of sessions with patient, date, status, and rating.
 * Click rows to navigate to session details.
 */

"use client"

import { useRouter } from "next/navigation"
import { AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useSessionList } from "@/hooks/useSessions"
import { QualityRating } from "./QualityRating"
import { SessionStatusBadge } from "./SessionStatusBadge"

export interface SessionsTableProps {
  className?: string
}

function formatSessionDate(dateString: string): string {
  const date = new Date(dateString)
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function SessionsTableSkeleton() {
  return (
    <div className="space-y-2" role="status" aria-label="Loading sessions">
      {[...Array(5)].map((_, i) => (
        <div
          key={i}
          className="h-16 bg-neutral-100 animate-pulse rounded-lg"
        />
      ))}
    </div>
  )
}

export function SessionsTable({ className }: SessionsTableProps) {
  const router = useRouter()
  const { data, isLoading, isError, error, refetch } = useSessionList()

  if (isLoading) {
    return <SessionsTableSkeleton />
  }

  if (isError) {
    return (
      <div
        className="card text-center py-12"
        role="alert"
        aria-live="assertive"
      >
        <AlertCircle className="w-12 h-12 text-red-500 mx-auto mb-4" />
        <h3 className="text-lg font-semibold text-neutral-900 mb-2">
          Failed to load sessions
        </h3>
        <p className="text-sm text-neutral-600 mb-4">
          {error instanceof Error ? error.message : "An error occurred"}
        </p>
        <Button onClick={() => refetch()} variant="outline">
          Try Again
        </Button>
      </div>
    )
  }

  const sessions = data?.data ?? []

  if (sessions.length === 0) {
    return (
      <div className="card text-center py-12">
        <p className="text-neutral-600">
          No sessions found. Upload a transcript to get started.
        </p>
      </div>
    )
  }

  return (
    <div className={cn("card overflow-hidden", className)}>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                Patient
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                Date
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                Session #
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                Status
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-neutral-600 uppercase tracking-wider">
                Rating
              </th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr
                key={session.id}
                onClick={() => router.push(`/dashboard/sessions/${session.id}`)}
                className="border-b border-neutral-100 hover:bg-neutral-50 cursor-pointer transition-colors"
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    router.push(`/dashboard/sessions/${session.id}`)
                  }
                }}
                aria-label={`View session for ${session.patient_name}`}
              >
                <td className="px-4 py-4 text-sm text-neutral-900">
                  {session.patient_name}
                </td>
                <td className="px-4 py-4 text-sm text-neutral-600">
                  {formatSessionDate(session.session_date)}
                </td>
                <td className="px-4 py-4 text-sm text-neutral-600">
                  {session.session_number}
                </td>
                <td className="px-4 py-4">
                  <SessionStatusBadge
                    status={session.status}
                    sessionId={session.id}
                    timestamp={session.note?.finalized_at ?? null}
                  />
                </td>
                <td className="px-4 py-4">
                  {session.note?.quality_rating != null ? (
                    <QualityRating
                      value={session.note.quality_rating}
                      readonly
                      size="sm"
                    />
                  ) : (
                    <span className="text-xs text-neutral-400">Not rated</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
