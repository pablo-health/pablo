// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SessionStatusBadge Component
 *
 * Display session status with color-coded badges and auto-polling for processing status.
 * Automatically refetches session data every 5 seconds when status is "processing".
 */

"use client"

import { Loader2, CheckCircle, AlertCircle, Clock, FileText } from "lucide-react"
import { cn } from "@/lib/utils"
import { useSession } from "@/hooks/useSessions"
import type { SessionStatus } from "@/types/sessions"

export interface SessionStatusBadgeProps {
  status: SessionStatus
  sessionId: string
  timestamp?: string | null
  className?: string
}

const statusConfig: Record<string, { label: string; className: string; icon: typeof Clock; animate?: boolean }> = {
  scheduled: {
    label: "Scheduled",
    className: "bg-blue-100 text-blue-700 border-blue-200",
    icon: Clock,
  },
  in_progress: {
    label: "In Progress",
    className: "bg-primary-100 text-primary-700 border-primary-200",
    icon: Loader2,
    animate: true,
  },
  recording_complete: {
    label: "Recording Complete",
    className: "bg-neutral-100 text-neutral-700 border-neutral-200",
    icon: FileText,
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-neutral-100 text-neutral-500 border-neutral-200",
    icon: AlertCircle,
  },
  queued: {
    label: "Queued",
    className: "bg-neutral-100 text-neutral-700 border-neutral-200",
    icon: Clock,
  },
  processing: {
    label: "Processing",
    className: "bg-primary-100 text-primary-700 border-primary-200",
    icon: Loader2,
    animate: true,
  },
  pending_review: {
    label: "Pending Review",
    className: "bg-amber-100 text-amber-700 border-amber-200",
    icon: FileText,
  },
  finalized: {
    label: "Finalized",
    className: "bg-secondary-100 text-secondary-700 border-secondary-200",
    icon: CheckCircle,
  },
  failed: {
    label: "Failed",
    className: "bg-red-100 text-red-700 border-red-200",
    icon: AlertCircle,
  },
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp)
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  })
}

export function SessionStatusBadge({
  status,
  sessionId,
  timestamp,
  className,
}: SessionStatusBadgeProps) {
  // Auto-poll when status is "processing"
  const { data: session } = useSession(sessionId, undefined, {
    refetchInterval: status === "processing" ? 5000 : false,
    enabled: status === "processing",
  })

  // Use updated status from polling if available
  const currentStatus = session?.status ?? status
  const config = statusConfig[currentStatus] ?? {
    label: currentStatus,
    className: "bg-neutral-100 text-neutral-600 border-neutral-200",
    icon: Clock,
  }
  const Icon = config.icon

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium border",
        config.className,
        currentStatus === "processing" && "animate-pulse",
        className
      )}
      role="status"
      aria-label={`Session status: ${config.label}`}
    >
      <Icon
        className={cn(
          "w-4 h-4",
          "animate" in config && config.animate && "animate-spin"
        )}
        aria-hidden="true"
      />
      <span>{config.label}</span>
      {currentStatus === "finalized" && timestamp && (
        <span className="text-xs opacity-75">
          • {formatTimestamp(timestamp)}
        </span>
      )}
    </div>
  )
}
