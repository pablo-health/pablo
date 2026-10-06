// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AddProblemRequest,
  Problem,
  ProblemListResponse,
  UpdateProblemRequest,
} from "@/types/problems"
import {
  addProblem,
  listProblems,
  removeProblem,
  reorderProblems,
  updateProblem,
} from "@/lib/api/problems"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Every write re-derives the chart's diagnosis line, so the patient refetches too. */
function afterWrite({ patientId }: { patientId: string }) {
  return [queryKeys.problems.byPatient(patientId), queryKeys.patients.detail(patientId)]
}

export function usePatientProblems(patientId: string | undefined) {
  return useAuthQuery<ProblemListResponse>({
    queryKey: queryKeys.problems.byPatient(patientId ?? ""),
    queryFn: () => listProblems(patientId!),
    enabled: !!patientId,
  })
}

export function useAddProblem() {
  return useAuthMutation<Problem, { patientId: string; data: AddProblemRequest }>({
    mutationFn: ({ patientId, data }) => addProblem(patientId, data),
    invalidateKeys: afterWrite,
  })
}

export function useUpdateProblem() {
  return useAuthMutation<
    Problem,
    { patientId: string; problemId: string; data: UpdateProblemRequest }
  >({
    mutationFn: ({ patientId, problemId, data }) => updateProblem(patientId, problemId, data),
    invalidateKeys: afterWrite,
  })
}

export function useReorderProblems() {
  return useAuthMutation<ProblemListResponse, { patientId: string; problemIds: string[] }>({
    mutationFn: ({ patientId, problemIds }) => reorderProblems(patientId, problemIds),
    invalidateKeys: afterWrite,
  })
}

export function useRemoveProblem() {
  return useAuthMutation<void, { patientId: string; problemId: string }>({
    mutationFn: ({ patientId, problemId }) => removeProblem(patientId, problemId),
    invalidateKeys: afterWrite,
  })
}
