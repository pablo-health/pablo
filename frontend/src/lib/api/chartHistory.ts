// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart history API client
 *
 * `GET    /api/patients/{patient_id}/chart-history` — every field, with earlier values,
 * `PUT    /api/patients/{patient_id}/chart-history/{key}` — record a value,
 * `DELETE /api/patients/{patient_id}/chart-history/{key}` — remove one that was never true.
 */

import type {
  ChartHistoryResponse,
  HistoryField,
  SetHistoryFieldRequest,
} from "@/types/chartHistory"
import { del, get, put } from "./client"

export async function getChartHistory(
  patientId: string,
  token?: string,
): Promise<ChartHistoryResponse> {
  return get<ChartHistoryResponse>(`/api/patients/${patientId}/chart-history`, token)
}

export async function setHistoryField(
  patientId: string,
  key: string,
  data: SetHistoryFieldRequest,
  token?: string,
): Promise<HistoryField> {
  return put<HistoryField>(`/api/patients/${patientId}/chart-history/${key}`, data, token)
}

export async function removeHistoryField(
  patientId: string,
  key: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/patients/${patientId}/chart-history/${key}`, token)
}
