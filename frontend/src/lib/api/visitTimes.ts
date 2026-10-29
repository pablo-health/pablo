// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ConfirmPsychotherapyWindowRequest, VisitTimes } from "@/types/visitTimes"
import { get, put } from "./client"

export async function getVisitTimes(sessionId: string, token?: string): Promise<VisitTimes> {
  return get<VisitTimes>(`/api/sessions/${sessionId}/visit-times`, token)
}

export async function confirmPsychotherapyWindow(
  sessionId: string,
  data: ConfirmPsychotherapyWindowRequest,
  token?: string,
): Promise<VisitTimes> {
  return put<VisitTimes>(`/api/sessions/${sessionId}/psychotherapy-window`, data, token)
}
