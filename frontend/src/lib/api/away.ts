// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Away dates API client
 *
 * `GET    /api/away-periods`              — current and upcoming periods, and who may cover,
 * `POST   /api/away-periods/preview`      — the suggested reply for these dates,
 * `POST   /api/away-periods`              — set away dates (409: they overlap others),
 * `PATCH  /api/away-periods/{id}`         — change them (422: new dates without the reply text),
 * `POST   /api/away-periods/{id}/end`     — end them now.
 */

import type {
  AcknowledgmentPreview,
  AwayDates,
  AwayPeriod,
  AwayPeriodList,
  CreateAwayPeriodRequest,
  UpdateAwayPeriodRequest,
} from "@/types/away"
import { get, patch, post } from "./client"

const BASE = "/api/away-periods"

export function getAwayPeriods(token?: string): Promise<AwayPeriodList> {
  return get<AwayPeriodList>(BASE, token)
}

export function previewAcknowledgment(
  dates: AwayDates,
  token?: string,
): Promise<AcknowledgmentPreview> {
  return post<AcknowledgmentPreview>(`${BASE}/preview`, dates, token)
}

export function createAwayPeriod(
  data: CreateAwayPeriodRequest,
  token?: string,
): Promise<AwayPeriod> {
  return post<AwayPeriod>(BASE, data, token)
}

export function updateAwayPeriod(
  periodId: string,
  data: UpdateAwayPeriodRequest,
  token?: string,
): Promise<AwayPeriod> {
  return patch<AwayPeriod>(`${BASE}/${periodId}`, data, token)
}

export function endAwayPeriod(periodId: string, token?: string): Promise<AwayPeriod> {
  return post<AwayPeriod>(`${BASE}/${periodId}/end`, {}, token)
}
