// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart proposals API client
 *
 * `GET  /api/notes/{note_id}/chart-proposals` — every proposal on the note,
 * `POST /api/notes/{note_id}/chart-proposals/retry` — check the note again after a failed check,
 * `POST /api/notes/{note_id}/chart-proposals/{proposal_id}/decision` — accept, edit or discard one.
 * `POST /api/patients/{patient_id}/intake-assignments/{assignment_id}/chart-proposals/{proposal_id}/decision`
 *   — the same, for one an accepted intake form proposed.
 */

import type {
  ChartProposal,
  ChartProposalsResponse,
  DecideChartProposalRequest,
  PatientChartProposalsResponse,
} from "@/types/chartProposals"
import { get, post } from "./client"

export async function getChartProposals(
  noteId: string,
  token?: string,
): Promise<ChartProposalsResponse> {
  return get<ChartProposalsResponse>(`/api/notes/${noteId}/chart-proposals`, token)
}

export async function decideChartProposal(
  noteId: string,
  proposalId: string,
  data: DecideChartProposalRequest,
  token?: string,
): Promise<ChartProposal> {
  return post<ChartProposal>(
    `/api/notes/${noteId}/chart-proposals/${proposalId}/decision`,
    data,
    token,
  )
}

export async function decideIntakeChartProposal(
  patientId: string,
  assignmentId: string,
  proposalId: string,
  data: DecideChartProposalRequest,
  token?: string,
): Promise<ChartProposal> {
  return post<ChartProposal>(
    `/api/patients/${patientId}/intake-assignments/${assignmentId}/chart-proposals/${proposalId}/decision`,
    data,
    token,
  )
}

export async function retryChartProposals(
  noteId: string,
  token?: string,
): Promise<ChartProposalsResponse> {
  return post<ChartProposalsResponse>(`/api/notes/${noteId}/chart-proposals/retry`, {}, token)
}

/** `GET /api/patients/{patient_id}/chart-proposals` — what is still pending across the client's notes. */
export async function getPatientChartProposals(
  patientId: string,
  token?: string,
): Promise<PatientChartProposalsResponse> {
  return get<PatientChartProposalsResponse>(`/api/patients/${patientId}/chart-proposals`, token)
}
