// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ApiError } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/queryKeys"
import {
  decideRefillRequest,
  fetchRefillQueue,
  type ClinicianRefillRequest,
  type DecideRefillInput,
  type RefillQueueResponse,
  type RefillQueueView,
} from "@/lib/api/refillRequests"
import { useAuthQuery } from "./useAuthQuery"

export function useRefillQueue(view: RefillQueueView, token?: string) {
  return useAuthQuery<RefillQueueResponse>({
    queryKey: queryKeys.refills.queue(view),
    queryFn: () => fetchRefillQueue(view, token),
  })
}

/**
 * Record a decision, then re-read both lists.
 *
 * A 409 means somebody at the practice answered the request first. The
 * screen is describing a request that is no longer waiting, so the lists are
 * re-read on that error too, and the request moves to where it now belongs.
 */
export function useDecideRefill(token?: string) {
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.refills.all })

  return useMutation<ClinicianRefillRequest, Error, DecideRefillInput>({
    mutationFn: (input) => decideRefillRequest(input, token),
    onSuccess: () => refresh(),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void refresh()
      }
    },
  })
}

export function isAlreadyAnswered(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
}
