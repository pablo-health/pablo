// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { addHomework, editHomework, getHomework, withdrawHomework } from "@/lib/api/homework"
import { queryKeys } from "@/lib/api/queryKeys"
import type { HomeworkAssignment, HomeworkOverview } from "@/types/homework"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

const afterWrite = ({ patientId }: { patientId: string }) => [
  queryKeys.homework.byPatient(patientId),
]

/**
 * A client's homework assignments. Suggestions from a note are chart updates,
 * decided at signing (`useChartProposals`); accepting one invalidates this.
 */
export function usePatientHomework(patientId: string | undefined) {
  return useAuthQuery<HomeworkOverview>({
    queryKey: queryKeys.homework.byPatient(patientId ?? ""),
    queryFn: () => getHomework(patientId!),
    enabled: !!patientId,
  })
}

export function useAddHomework() {
  return useAuthMutation<HomeworkAssignment, { patientId: string; text: string }>({
    mutationFn: ({ patientId, text }) => addHomework(patientId, text),
    invalidateKeys: afterWrite,
  })
}

export function useWithdrawHomework() {
  return useAuthMutation<HomeworkAssignment, { patientId: string; assignmentId: string }>({
    mutationFn: ({ patientId, assignmentId }) => withdrawHomework(patientId, assignmentId),
    invalidateKeys: afterWrite,
  })
}

export function useEditHomework() {
  return useAuthMutation<
    HomeworkAssignment,
    { patientId: string; assignmentId: string; text: string }
  >({
    mutationFn: ({ patientId, assignmentId, text }) => editHomework(patientId, assignmentId, text),
    invalidateKeys: afterWrite,
  })
}
