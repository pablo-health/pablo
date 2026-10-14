// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useInfiniteQuery } from "@tanstack/react-query"
import {
  getRecordActivity,
  type RecordActivityItem,
  type RecordActivityPage,
} from "@/lib/api/patientActivity"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuth } from "@/lib/auth-context"

export const RECORD_ACTIVITY_PAGE_SIZE = 50

/**
 * Who acted on a client's record, newest first, paged back on demand.
 *
 * Used by the panel's body, which mounts only while the panel is open.
 * Reading the trail writes a row of its own, so this never refetches on focus
 * or reconnect: each opening is one read, and one recorded access.
 */
export function useRecordActivity(patientId: string) {
  const { loading } = useAuth()

  const query = useInfiniteQuery<RecordActivityPage>({
    queryKey: queryKeys.recordActivity(patientId),
    queryFn: ({ pageParam }) =>
      getRecordActivity(patientId, {
        cursor: pageParam as string | null,
        limit: RECORD_ACTIVITY_PAGE_SIZE,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: !loading,
    // Gone as soon as the panel closes, so the next opening is a fresh read
    // rather than a cached page that may be missing what happened since.
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const entries: RecordActivityItem[] = query.data?.pages.flatMap((page) => page.data) ?? []

  return {
    entries,
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
    hasMore: query.hasNextPage,
    loadMore: query.fetchNextPage,
    isLoadingMore: query.isFetchingNextPage,
  }
}
