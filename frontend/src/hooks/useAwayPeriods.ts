// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  createAwayPeriod,
  endAwayPeriod,
  getAwayPeriods,
  previewAcknowledgment,
  updateAwayPeriod,
} from "@/lib/api/away"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  AcknowledgmentPreview,
  AwayDates,
  AwayPeriod,
  AwayPeriodList,
  CreateAwayPeriodRequest,
  UpdateAwayPeriodRequest,
} from "@/types/away"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

const afterWrite = [queryKeys.away.all] as const

/** The caller's current and upcoming away dates, and the colleagues who may cover. */
export function useAwayPeriods() {
  return useAuthQuery<AwayPeriodList>({
    queryKey: queryKeys.away.all,
    queryFn: () => getAwayPeriods(),
  })
}

/** The suggested reply for these dates. Nothing is stored. */
export function usePreviewAcknowledgment() {
  return useAuthMutation<AcknowledgmentPreview, AwayDates>({
    mutationFn: (dates) => previewAcknowledgment(dates),
  })
}

export function useCreateAwayPeriod() {
  return useAuthMutation<AwayPeriod, CreateAwayPeriodRequest>({
    mutationFn: (data) => createAwayPeriod(data),
    invalidateKeys: afterWrite,
  })
}

export function useUpdateAwayPeriod() {
  return useAuthMutation<AwayPeriod, { periodId: string; data: UpdateAwayPeriodRequest }>({
    mutationFn: ({ periodId, data }) => updateAwayPeriod(periodId, data),
    invalidateKeys: afterWrite,
  })
}

export function useEndAwayPeriod() {
  return useAuthMutation<AwayPeriod, string>({
    mutationFn: (periodId) => endAwayPeriod(periodId),
    invalidateKeys: afterWrite,
  })
}
