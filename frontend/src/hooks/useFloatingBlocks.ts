// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQueries } from "@tanstack/react-query"
import type {
  AvailabilityRule,
  FloatingBlock,
  FloatingBlockException,
  FloatingBlocksResponse,
} from "@/types/availability"
import {
  clearFloatingBlockException,
  getFloatingBlocks,
  setFloatingBlockException,
} from "@/lib/api/availability"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuth } from "@/lib/auth-context"
import { calendarFreshness } from "./calendarFreshness"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * Where a floating block sits is derived from the day as it stands — the
 * appointments and calendar busy time already on it — so it is read on the
 * calendar's own freshness schedule: a session moved in the clinician's
 * calendar app can move lunch with it.
 */
const FLOATING_STALE_MS = 60 * 1000

const NO_BLOCKS: FloatingBlock[] = []

/** Each floating block on one date ("YYYY-MM-DD"). */
export function useFloatingBlocks(date: string, token?: string) {
  return useAuthQuery<FloatingBlocksResponse>({
    queryKey: queryKeys.availability.floatingBlocks(date),
    queryFn: () => getFloatingBlocks(date, token),
    staleTime: FLOATING_STALE_MS,
    ...calendarFreshness,
    enabled: !!date,
  })
}

/**
 * Each floating block on every date given, flattened: the week view asks
 * for seven days at once. One request per date, because that is the shape
 * of the endpoint, each cached under the same key `useFloatingBlocks` uses.
 */
export function useFloatingBlocksForDates(dates: string[], enabled = true): FloatingBlock[] {
  const { loading } = useAuth()
  return useQueries({
    queries: dates.map((date) => ({
      queryKey: queryKeys.availability.floatingBlocks(date),
      queryFn: () => getFloatingBlocks(date),
      staleTime: FLOATING_STALE_MS,
      ...calendarFreshness,
      enabled: enabled && !loading && !!date,
    })),
    combine: flattenBlocks,
  })
}

// Module-level so React Query keeps the combined array stable between
// renders while no result changes.
function flattenBlocks(results: { data?: FloatingBlocksResponse }[]): FloatingBlock[] {
  if (results.every((result) => !result.data?.blocks.length)) return NO_BLOCKS
  return results.flatMap((result) => result.data?.blocks ?? NO_BLOCKS)
}

// A write moves the bands, and also what can be booked around them, so the
// whole availability prefix goes: rules, slots and every cached day.
const AFTER_EXCEPTION_WRITE = [queryKeys.availability.all] as const

/** Pin a floating block to a start on one date, or skip it that date. */
export function useSetFloatingBlockException(token?: string) {
  return useAuthMutation<
    AvailabilityRule,
    { ruleId: string; date: string; exception: FloatingBlockException }
  >({
    mutationFn: ({ ruleId, date, exception }) =>
      setFloatingBlockException(ruleId, date, exception, token),
    invalidateKeys: AFTER_EXCEPTION_WRITE,
  })
}

/** Let a floating block float again on one date. */
export function useClearFloatingBlockException(token?: string) {
  return useAuthMutation<AvailabilityRule, { ruleId: string; date: string }>({
    mutationFn: ({ ruleId, date }) => clearFloatingBlockException(ruleId, date, token),
    invalidateKeys: AFTER_EXCEPTION_WRITE,
  })
}
