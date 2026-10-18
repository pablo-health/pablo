// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Homework API client (the clinician's side)
 *
 * `GET   /api/patients/{patient_id}/homework` — the assignments,
 * `POST  /api/patients/{patient_id}/homework` — add one by hand,
 * `POST  /api/patients/{patient_id}/homework/{id}/withdraw` — take one back,
 * `PATCH /api/patients/{patient_id}/homework/{id}` — reword an open one.
 */

import type { HomeworkAssignment, HomeworkOverview } from "@/types/homework"
import { get, patch, post } from "./client"

const base = (patientId: string) => `/api/patients/${patientId}/homework`

export function getHomework(patientId: string, token?: string): Promise<HomeworkOverview> {
  return get<HomeworkOverview>(base(patientId), token)
}

export function addHomework(
  patientId: string,
  text: string,
  token?: string,
): Promise<HomeworkAssignment> {
  return post<HomeworkAssignment>(base(patientId), { text }, token)
}

export function withdrawHomework(
  patientId: string,
  assignmentId: string,
  token?: string,
): Promise<HomeworkAssignment> {
  return post<HomeworkAssignment>(`${base(patientId)}/${assignmentId}/withdraw`, {}, token)
}

export function editHomework(
  patientId: string,
  assignmentId: string,
  text: string,
  token?: string,
): Promise<HomeworkAssignment> {
  return patch<HomeworkAssignment>(`${base(patientId)}/${assignmentId}`, { text }, token)
}
