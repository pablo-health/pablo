// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useInfiniteQuery } from "@tanstack/react-query"
import { getMyAuditLog, type AuditLogItem, type AuditLogPage } from "@/lib/api/users"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuth } from "@/lib/auth-context"

/** Rows per request. Small enough to render fast, large enough that a normal
 * week of work is one page. */
export const AUDIT_LOG_PAGE_SIZE = 50

/**
 * The caller's own audit trail, newest first, paged backwards on demand.
 *
 * Deliberately NOT self-refreshing. Reading the audit log writes an audit
 * row (the server records that you looked), so anything that refetches on a
 * timer or on window focus would generate the very rows it then displays —
 * a log that grows because you are watching it. The list is fetched when the
 * page opens and extended only when the reader asks for more.
 */
export function useAuditLog() {
  const { loading } = useAuth()

  const query = useInfiniteQuery<AuditLogPage>({
    queryKey: queryKeys.user.auditLog(),
    queryFn: ({ pageParam }) =>
      getMyAuditLog({
        cursor: pageParam as string | null,
        limit: AUDIT_LOG_PAGE_SIZE,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: !loading,
    // The trail is append-only history: once fetched, a page cannot change
    // underneath us, so there is nothing to go stale.
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnReconnect: false,
  })

  const entries: AuditLogItem[] = query.data?.pages.flatMap((page) => page.data) ?? []

  return {
    entries,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    hasMore: query.hasNextPage,
    loadMore: query.fetchNextPage,
    isLoadingMore: query.isFetchingNextPage,
  }
}
