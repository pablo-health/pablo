// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  ChartHistoryResponse,
  HistoryField,
  SetHistoryFieldRequest,
} from "@/types/chartHistory"
import { getChartHistory, removeHistoryField, setHistoryField } from "@/lib/api/chartHistory"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

function afterWrite({ patientId }: { patientId: string }) {
  return [queryKeys.chartHistory.byPatient(patientId)]
}

export function usePatientChartHistory(patientId: string | undefined) {
  return useAuthQuery<ChartHistoryResponse>({
    queryKey: queryKeys.chartHistory.byPatient(patientId ?? ""),
    queryFn: () => getChartHistory(patientId!),
    enabled: !!patientId,
  })
}

export function useSetHistoryField() {
  return useAuthMutation<
    HistoryField,
    { patientId: string; key: string; data: SetHistoryFieldRequest }
  >({
    mutationFn: ({ patientId, key, data }) => setHistoryField(patientId, key, data),
    invalidateKeys: afterWrite,
  })
}

export function useRemoveHistoryField() {
  return useAuthMutation<void, { patientId: string; key: string }>({
    mutationFn: ({ patientId, key }) => removeHistoryField(patientId, key),
    invalidateKeys: afterWrite,
  })
}
