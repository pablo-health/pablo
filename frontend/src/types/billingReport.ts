// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's financial report API types.
 *
 * Mirrors `app.models.billing_report` field for field. Every rate here is
 * left as cents on the wire — `collections_rate` carries the four figures a
 * rate divides out of, not a percentage — so rounding happens exactly once,
 * in the component that renders it.
 */

export interface AgingBucketResponse {
  label: string
  count: number
  cents: number
}

export interface AgingReportResponse {
  buckets: AgingBucketResponse[]
}

export interface PayerMixEntryResponse {
  payer_id: string
  payer_name: string
  billed_cents: number
  collected_cents: number
}

export interface PayerMixReportResponse {
  entries: PayerMixEntryResponse[]
}

/** `collected / (billed - contractual_adjustment - write_off)` is the rate. */
export interface CollectionsRateResponse {
  billed_cents: number
  collected_cents: number
  contractual_adjustment_cents: number
  write_off_cents: number
}

export interface PayerLagResponse {
  payer_id: string
  payer_name: string
  claim_count: number
  median_days: number
  p90_days: number
}

export interface ClaimPaymentLagReportResponse {
  by_payer: PayerLagResponse[]
}

export interface BillingReportResponse {
  from_date: string
  to_date: string
  aging: AgingReportResponse
  payer_mix: PayerMixReportResponse
  collections_rate: CollectionsRateResponse
  claim_payment_lag: ClaimPaymentLagReportResponse
}
