// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Export Queue React Query Hooks
 *
 * Custom hooks for admin export queue management using React Query.
 * Includes cache invalidation and error handling.
 */

"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { ExportActionRequest } from "@/types/sessions"
import { listExportQueue, performExportAction } from "@/lib/api/admin"
import { queryKeys } from "@/lib/api/queryKeys"

// ============================================================================
// QUERY HOOKS (Read Operations)
// ============================================================================

/**
 * Fetch list of sessions pending export review
 *
 * Returns all sessions with export_status=pending_review across all users.
 * Requires admin privileges (bypassed in dev mode).
 *
 * @param token - Optional auth token for server-side queries
 *
 * @example
 * function ExportReviewPage() {
 *   const { data, isLoading } = useExportQueue()
 *   if (isLoading) return <div>Loading...</div>
 *   return (
 *     <div>
 *       <h1>Export Queue ({data.total} sessions)</h1>
 *       {data.data.map(session => (
 *         <ExportReviewCard key={session.id} session={session} />
 *       ))}
 *     </div>
 *   )
 * }
 */
export function useExportQueue(token?: string) {
  return useQuery({
    queryKey: queryKeys.admin.exportQueue(),
    queryFn: () => listExportQueue(token),
    staleTime: 30 * 1000, // 30 seconds
  })
}

// ============================================================================
// MUTATION HOOKS (Write Operations)
// ============================================================================

/**
 * Perform action on queued export session
 *
 * Actions:
 * - approve: Set status to "approved" (ready for export)
 * - skip: Set status to "skipped" (remove from queue)
 * - flag: Set status to "skipped" with reason (PII concern)
 *
 * Features:
 * - Automatic cache invalidation for export queue
 * - Error handling for invalid actions
 *
 * @param token - Optional auth token
 *
 * @example
 * function ExportReviewCard({ session }: { session: ExportQueueItem }) {
 *   const actionMutation = useExportAction()
 *
 *   const handleApprove = async () => {
 *     await actionMutation.mutateAsync({
 *       sessionId: session.id,
 *       data: { action: "approve" }
 *     })
 *   }
 *
 *   const handleFlag = async () => {
 *     await actionMutation.mutateAsync({
 *       sessionId: session.id,
 *       data: {
 *         action: "flag",
 *         reason: "Patient name not fully redacted"
 *       }
 *     })
 *   }
 *
 *   return (
 *     <div>
 *       <button onClick={handleApprove} disabled={actionMutation.isPending}>
 *         Approve
 *       </button>
 *       <button onClick={handleFlag} disabled={actionMutation.isPending}>
 *         Flag
 *       </button>
 *     </div>
 *   )
 * }
 */
export function useExportAction(token?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      sessionId,
      data,
    }: {
      sessionId: string
      data: ExportActionRequest
    }) => performExportAction(sessionId, data, token),

    onSuccess: () => {
      // Invalidate export queue to refresh the list
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.exportQueue() })
    },
  })
}
