// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Problem list API client
 *
 * `GET    /api/patients/{patient_id}/problems` — the list, in order,
 * `POST   /api/patients/{patient_id}/problems` — add one (409 if already listed),
 * `PATCH  /api/patients/{patient_id}/problems/{id}` — edit, resolve, reactivate,
 * `PUT    /api/patients/{patient_id}/problems/order` — reorder,
 * `DELETE /api/patients/{patient_id}/problems/{id}` — remove one entered in error.
 */

import type {
  AddProblemRequest,
  Problem,
  ProblemListResponse,
  UpdateProblemRequest,
} from "@/types/problems"
import { del, get, patch, post, put } from "./client"

export async function listProblems(
  patientId: string,
  token?: string,
): Promise<ProblemListResponse> {
  return get<ProblemListResponse>(`/api/patients/${patientId}/problems`, token)
}

export async function addProblem(
  patientId: string,
  data: AddProblemRequest,
  token?: string,
): Promise<Problem> {
  return post<Problem>(`/api/patients/${patientId}/problems`, data, token)
}

export async function updateProblem(
  patientId: string,
  problemId: string,
  data: UpdateProblemRequest,
  token?: string,
): Promise<Problem> {
  return patch<Problem>(`/api/patients/${patientId}/problems/${problemId}`, data, token)
}

export async function reorderProblems(
  patientId: string,
  problemIds: string[],
  token?: string,
): Promise<ProblemListResponse> {
  return put<ProblemListResponse>(
    `/api/patients/${patientId}/problems/order`,
    { problem_ids: problemIds },
    token,
  )
}

export async function removeProblem(
  patientId: string,
  problemId: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/patients/${patientId}/problems/${problemId}`, token)
}
