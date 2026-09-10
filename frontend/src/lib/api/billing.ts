// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The billing page's two working queues.
 *
 * The unbilled queue (`app.routes.billing_queue`) answers "what have I not
 * charged for yet"; balances (`app.routes.practice_balances`) answers "who
 * has not paid". They are different questions — a session charged and
 * declined is off the first and on the second.
 */

import type { BillingReportResponse } from "@/types/billingReport"
import type { UnbilledQueueResponse } from "@/types/billing"
import type { BalancesResponse } from "@/types/payments"
import { get } from "./client"

export async function fetchUnbilledQueue(token?: string): Promise<UnbilledQueueResponse> {
  return get<UnbilledQueueResponse>("/api/billing/unbilled-sessions", token)
}

/** Every client carrying a balance, oldest outstanding first. */
export async function fetchBalances(token?: string): Promise<BalancesResponse> {
  return get<BalancesResponse>("/api/billing/balances", token)
}

/** Aging, payer mix, collections rate and claim-to-payment lag for a window. */
export async function fetchBillingReport(
  from: string,
  to: string,
  token?: string,
): Promise<BillingReportResponse> {
  const query = new URLSearchParams({ from, to })
  return get<BillingReportResponse>(`/api/billing/report?${query.toString()}`, token)
}
