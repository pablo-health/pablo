// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Query, QueryKey } from "@tanstack/react-query"
import { runAsBackground } from "@/lib/api/backgroundRequests"

/**
 * How often a query re-reads: milliseconds, `false` for not now, or a
 * function of the query that returns either (React Query's own shape).
 */
export type PollInterval<TData> =
  | number
  | false
  | ((query: Query<TData, Error, TData, QueryKey>) => number | false | undefined)

type QueryFnData<TOptions> = TOptions extends {
  queryFn?: infer TQueryFn
}
  ? TQueryFn extends (...args: never[]) => infer TResult
    ? Awaited<TResult>
    : never
  : never

/**
 * Make a query poll without its polls counting as someone using the app.
 *
 * Returns `options` with `refetchInterval` set and its `queryFn` wrapped so
 * every request the query makes is sent as a background request, which the
 * API checks against the idle sign-out but does not treat as activity. This
 * is the only place `refetchInterval` may be written (lint enforces it): a
 * poll that refreshed the idle clock would keep an unattended screen signed
 * in. Works for the options of `useQuery`, `useAuthQuery`, and each entry
 * of `useQueries`.
 *
 * The `queryFn` must start its request synchronously — call the API function
 * before any `await` of its own. The mark only lasts while the `queryFn` is
 * running synchronously; a request begun after an await goes out unmarked
 * and resets the idle clock. Each poller has a test that pins this.
 */
export function pollingOptions<TOptions extends { queryFn?: unknown }>(
  options: TOptions,
  interval: PollInterval<QueryFnData<TOptions>>,
): TOptions & { refetchInterval: PollInterval<QueryFnData<TOptions>> } {
  const { queryFn } = options
  return {
    ...options,
    ...(typeof queryFn === "function"
      ? {
          queryFn: (...args: unknown[]) =>
            runAsBackground(() => (queryFn as (...a: unknown[]) => unknown)(...args)),  // TOptions only promises queryFn is some function; the wrapper forwards its arguments untouched
        }
      : {}),
    refetchInterval: interval,
  }
}
