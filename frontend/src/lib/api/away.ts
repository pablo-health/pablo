// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Away dates API client
 *
 * `GET    /api/away-periods`              — current and upcoming periods, and who may cover,
 * `POST   /api/away-periods/preview`      — the suggested reply for these dates,
 * `POST   /api/away-periods`              — set away dates (409: they overlap others),
 * `PATCH  /api/away-periods/{id}`         — change them (422: new dates without the reply text),
 * `POST   /api/away-periods/{id}/end`     — end them now,
 * `GET    /api/away-periods/{id}/coverage-activity` — what the covering colleague did,
 * `GET    /api/covering`                  — away dates the caller is asked to cover,
 * `POST   /api/covering/{id}/accept`      — cover them (409: already covering that colleague),
 * `POST   /api/covering/{id}/decline`     — don't, or stop.
 */

import type {
  AcknowledgmentPreview,
  AwayDates,
  AwayPeriod,
  AwayPeriodList,
  CoverageActivity,
  CoverageRequest,
  CoverageRequestList,
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

export function getCoverageActivity(periodId: string, token?: string): Promise<CoverageActivity> {
  return get<CoverageActivity>(`${BASE}/${periodId}/coverage-activity`, token)
}

const COVERING = "/api/covering"

export function getCoverageRequests(token?: string): Promise<CoverageRequestList> {
  return get<CoverageRequestList>(COVERING, token)
}

export function answerCoverageRequest(
  periodId: string,
  answer: "accept" | "decline",
  token?: string,
): Promise<CoverageRequest> {
  return post<CoverageRequest>(`${COVERING}/${periodId}/${answer}`, {}, token)
}
