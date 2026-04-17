// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type QueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"
import { useAuth } from "@/lib/auth-context"

/**
 * useQuery wrapper that defers execution until Firebase auth resolves.
 * Merges caller's `enabled` with the auth loading state.
 */
export function useAuthQuery<
  TQueryFnData = unknown,
  TError = Error,
  TData = TQueryFnData,
  TQueryKey extends QueryKey = QueryKey,
>(options: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>) {
  const { loading } = useAuth()
  return useQuery({
    ...options,
    enabled: (options.enabled ?? true) && !loading,
  })
}

// useAuthMutation

type InvalidateKeysFn<TVariables, TData> = (
  variables: TVariables,
  data?: TData,
) => readonly (readonly unknown[])[]

interface OptimisticConfig<TCached, TVariables> {
  /** Derive the cache key for the entity being updated from the mutation variables. */
  queryKey: (variables: TVariables) => readonly unknown[]
  /** Produce the optimistically-updated cache entry. */
  updater: (previous: TCached, variables: TVariables) => TCached
}

interface UseAuthMutationOptions<TData, TVariables, TCached = unknown> {
  mutationFn: (variables: TVariables) => Promise<TData>
  /** Query keys to invalidate. Static array or function of (variables, data). */
  invalidateKeys?:
    | readonly (readonly unknown[])[]
    | InvalidateKeysFn<TVariables, TData>
  /** Optimistic update — generates onMutate/onError automatically. */
  optimistic?: OptimisticConfig<TCached, TVariables>
  /** Runs before cache invalidation. Receives queryClient for custom cache ops. */
  onSuccess?: (
    data: TData,
    variables: TVariables,
    queryClient: QueryClient,
  ) => void
}

/**
 * useMutation wrapper with standard cache invalidation and optional optimistic updates.
 *
 * Without `optimistic`: invalidates keys in onSuccess.
 * With `optimistic`: applies optimistic cache update in onMutate, rolls back
 * in onError, then invalidates keys in onSettled.
 */
export function useAuthMutation<TData, TVariables = void, TCached = unknown>(
  options: UseAuthMutationOptions<TData, TVariables, TCached>,
) {
  const queryClient = useQueryClient()

  const resolveKeys = (variables: TVariables, data?: TData) => {
    const { invalidateKeys } = options
    if (!invalidateKeys) return []
    return typeof invalidateKeys === "function"
      ? invalidateKeys(variables, data)
      : invalidateKeys
  }

  const invalidate = (variables: TVariables, data?: TData) => {
    for (const key of resolveKeys(variables, data)) {
      queryClient.invalidateQueries({ queryKey: key })
    }
  }

  return useMutation<
    TData,
    Error,
    TVariables,
    { previous?: TCached; queryKey?: readonly unknown[] }
  >({
    mutationFn: options.mutationFn,

    onMutate: options.optimistic
      ? async (variables: TVariables) => {
          const key = options.optimistic!.queryKey(variables)
          await queryClient.cancelQueries({ queryKey: key })
          const previous = queryClient.getQueryData<TCached>(key)
          if (previous) {
            queryClient.setQueryData<TCached>(
              key,
              options.optimistic!.updater(previous, variables),
            )
          }
          return { previous, queryKey: key }
        }
      : undefined,

    onError: options.optimistic
      ? (_error, _variables, context) => {
          if (context?.previous && context.queryKey) {
            queryClient.setQueryData(context.queryKey, context.previous)
          }
        }
      : undefined,

    onSuccess: (data, variables) => {
      options.onSuccess?.(data, variables, queryClient)
      if (!options.optimistic) invalidate(variables, data)
    },

    onSettled: options.optimistic
      ? (_data, _error, variables) => invalidate(variables)
      : undefined,
  })
}
