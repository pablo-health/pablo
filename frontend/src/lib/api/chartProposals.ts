// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart proposals API client
 *
 * `GET  /api/notes/{note_id}/chart-proposals` — every proposal on the note,
 * `POST /api/notes/{note_id}/chart-proposals/{proposal_id}/decision` — accept, edit or discard one.
 */

import type {
  ChartProposal,
  ChartProposalsResponse,
  DecideChartProposalRequest,
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
