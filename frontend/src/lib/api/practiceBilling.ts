// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice billing profile API functions.
 *
 * The legal entity a claim is filed as, and the billing-side switches that
 * ride on the same row. See backend/app/routes/practice_billing.py.
 */

import type {
  BillingProfileResponse,
  UpdateBillingProfileRequest,
} from "@/types/practiceBilling"
import { get, patch } from "./client"

const ENDPOINT = "/api/practice/billing-profile"

export async function getBillingProfile(token?: string): Promise<BillingProfileResponse> {
  return get<BillingProfileResponse>(ENDPOINT, token)
}

export async function updateBillingProfile(
  data: UpdateBillingProfileRequest,
  token?: string,
): Promise<BillingProfileResponse> {
  return patch<BillingProfileResponse>(ENDPOINT, data, token)
}
