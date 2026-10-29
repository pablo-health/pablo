// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  ChartProposal,
  ChartProposalsResponse,
  DecideChartProposalRequest,
  PatientChartProposalsResponse,
} from "@/types/chartProposals"
import {
  decideChartProposal,
  decideIntakeChartProposal,
  getChartProposals,
  getPatientChartProposals,
  retryChartProposals,
} from "@/lib/api/chartProposals"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"
import { withDashboard } from "./dashboardFreshness"

export function useChartProposals(noteId: string | undefined) {
  return useAuthQuery<ChartProposalsResponse>({
    queryKey: queryKeys.chartProposals.byNote(noteId ?? ""),
    queryFn: () => getChartProposals(noteId!),
    enabled: !!noteId,
  })
}

/** What is still pending across the client's notes, grouped by note. */
export function usePatientChartProposals(patientId: string | undefined) {
  return useAuthQuery<PatientChartProposalsResponse>({
    queryKey: queryKeys.chartProposals.byPatient(patientId ?? ""),
    queryFn: () => getPatientChartProposals(patientId!),
    enabled: !!patientId,
  })
}

/**
 * Where a proposal came from, which is the route it is decided through: a note,
 * or an accepted intake form (`intakeAssignmentId`).
 */
export interface ProposalSource {
  noteId?: string
  intakeAssignmentId?: string
}

/**
 * Accept, edit or discard one; an accept or an edit changes the chart, so it
 * is read again. Every listing of proposals is refreshed, the note's and the
 * client's: the same proposal can be on screen in both. So is the dashboard,
 * whose Today panel counts what is still waiting, and the task lists, which
 * an accepted task from a session joins.
 */
export function useDecideChartProposal(patientId: string) {
  return useAuthMutation<
    ChartProposal,
    ProposalSource & { proposalId: string; data: DecideChartProposalRequest }
  >({
    mutationFn: ({ noteId, intakeAssignmentId, proposalId, data }) =>
      intakeAssignmentId
        ? decideIntakeChartProposal(patientId, intakeAssignmentId, proposalId, data)
        : decideChartProposal(noteId ?? "", proposalId, data),
    invalidateKeys: () =>
      withDashboard([
        queryKeys.chartProposals.all,
        queryKeys.chartHistory.byPatient(patientId),
        queryKeys.patients.detail(patientId),
        queryKeys.tasks.all,
        // An accepted homework suggestion is a new assignment.
        queryKeys.homework.byPatient(patientId),
      ]),
  })
}

/** Check the note for chart updates again, after a check that failed. */
export function useRetryChartProposals() {
  return useAuthMutation<ChartProposalsResponse, { noteId: string }>({
    mutationFn: ({ noteId }) => retryChartProposals(noteId),
    invalidateKeys: ({ noteId }) =>
      withDashboard([queryKeys.chartProposals.byNote(noteId)]),
  })
}
