// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  ChartProposal,
  ChartProposalsResponse,
  DecideChartProposalRequest,
} from "@/types/chartProposals"
import {
  decideChartProposal,
  getChartProposals,
  retryChartProposals,
} from "@/lib/api/chartProposals"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function useChartProposals(noteId: string | undefined) {
  return useAuthQuery<ChartProposalsResponse>({
    queryKey: queryKeys.chartProposals.byNote(noteId ?? ""),
    queryFn: () => getChartProposals(noteId!),
    enabled: !!noteId,
  })
}

/** Accept, edit or discard one; an accept or an edit changes the chart, so it is read again. */
export function useDecideChartProposal(patientId: string) {
  return useAuthMutation<
    ChartProposal,
    { noteId: string; proposalId: string; data: DecideChartProposalRequest }
  >({
    mutationFn: ({ noteId, proposalId, data }) => decideChartProposal(noteId, proposalId, data),
    invalidateKeys: ({ noteId }) => [
      queryKeys.chartProposals.byNote(noteId),
      queryKeys.chartHistory.byPatient(patientId),
      queryKeys.patients.detail(patientId),
    ],
  })
}

/** Check the note for chart updates again, after a check that failed. */
export function useRetryChartProposals() {
  return useAuthMutation<ChartProposalsResponse, { noteId: string }>({
    mutationFn: ({ noteId }) => retryChartProposals(noteId),
    invalidateKeys: ({ noteId }) => [queryKeys.chartProposals.byNote(noteId)],
  })
}
