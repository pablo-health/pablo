// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Scheduling policy API functions.
 *
 * The practice-wide defaults an appointment type falls back to (notice,
 * cancel/reschedule cutoffs) and the new-patient / self-booking switches. See
 * backend/app/scheduling_engine/services/scheduling_policy.py.
 */

import type {
  SchedulingPolicyResponse,
  UpdateSchedulingPolicyRequest,
} from "@/types/scheduling"
import { get, patch } from "./client"

const ENDPOINT = "/api/scheduling/policy"

export async function getSchedulingPolicy(token?: string): Promise<SchedulingPolicyResponse> {
  return get<SchedulingPolicyResponse>(ENDPOINT, token)
}

export async function updateSchedulingPolicy(
  data: UpdateSchedulingPolicyRequest,
  token?: string
): Promise<SchedulingPolicyResponse> {
  return patch<SchedulingPolicyResponse>(ENDPOINT, data, token)
}
