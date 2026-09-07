// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Type-safe claim fixtures for the billing claims tests. */

import type {
  ClaimDeadlines,
  ClaimDetailResponse,
  ClaimHop,
  ClaimLine,
  ClaimReceipt,
  ClaimReceiptKind,
  ClaimTrackerItem,
  SubmissionFinding,
} from "@/types/claims"

/**
 * One thing the clearinghouse said was wrong, in its own words. Shared by the
 * detail and the tracker suites so both talk about the same claim: the detail
 * shows this description, and the tracker row must not.
 */
export const VENDOR_FINDING: SubmissionFinding = {
  source: "edit",
  code: "A7:562",
  description: "Entity's National Provider Identifier (NPI) is not on file.",
  followup_action: "Resubmit once the NPI is registered with this payer.",
}

export const NO_DEADLINE: ClaimDeadlines = {
  filing: null,
  correction: null,
  appeal: null,
  applicable: null,
  days_left: null,
}

export function line(overrides: Partial<ClaimLine> = {}): ClaimLine {
  return {
    id: "line-1",
    claim_id: "claim-1",
    patient_id: "patient-1",
    appointment_id: "appt-1",
    line_number: 1,
    line_control_number: "886598911",
    service_date: "2026-09-01",
    cpt: "90837",
    modifiers: ["95"],
    units: 1,
    charge_cents: 15000,
    dx_pointers: [1],
    telehealth: true,
    allowed_cents: null,
    paid_cents: 0,
    patient_resp_cents: null,
    adjustments: null,
    created_at: "2026-09-02T15:00:00Z",
    ...overrides,
  }
}

export function hops(reachedThrough: number): ClaimHop[] {
  const kinds = [
    "built",
    "submitted",
    "clearinghouse_accepted",
    "payer_accepted",
    "adjudicated",
  ] as const
  return kinds.map((kind, index) => ({
    kind,
    reached: index <= reachedThrough,
    at: index <= reachedThrough ? "2026-09-02T15:00:00Z" : null,
  }))
}

export function receipt(
  kind: ClaimReceiptKind,
  overrides: Partial<ClaimReceipt> = {},
): ClaimReceipt {
  return {
    id: `receipt-${kind}`,
    claim_id: "claim-1",
    kind,
    from_state: "submitted",
    to_state: "submitted",
    deadline_kind: null,
    rung: null,
    vendor_event_id: null,
    vendor_transaction_id: null,
    detail: {},
    occurred_at: "2026-09-02T15:00:00Z",
    ...overrides,
  }
}

export function claimDetail(overrides: Partial<ClaimDetailResponse> = {}): ClaimDetailResponse {
  return {
    id: "claim-1",
    control_number: "88659891",
    patient_id: "patient-1",
    coverage_id: "cov-1",
    payer_id: "payer-1",
    state: "draft",
    frequency_code: "1",
    parent_claim_id: null,
    total_charge_cents: 15000,
    total_paid_cents: 0,
    diagnosis_codes: ["F41.1"],
    place_of_service: "10",
    submitted_at: null,
    payer_accepted_at: null,
    adjudicated_at: null,
    vendor_claim_id: null,
    payer_claim_number: null,
    submission_pending_at: null,
    submission_findings: [],
    last_receipt_at: null,
    status_checked_at: null,
    created_at: "2026-09-02T15:00:00Z",
    updated_at: "2026-09-02T15:00:00Z",
    lines: [line()],
    patient_name: "Ada Early",
    payer_name: "Test Payer",
    findings: [],
    hops: hops(0),
    deadlines: NO_DEADLINE,
    receipts: [],
    next_action: "review_and_file",
    ...overrides,
  }
}

export function trackerItem(overrides: Partial<ClaimTrackerItem> = {}): ClaimTrackerItem {
  return {
    id: "claim-1",
    control_number: "88659891",
    patient_id: "patient-1",
    patient_name: "Ada Early",
    payer_id: "payer-1",
    payer_name: "Test Payer",
    state: "draft",
    frequency_code: "1",
    parent_claim_id: null,
    service_date: "2026-09-01",
    total_charge_cents: 15000,
    total_paid_cents: 0,
    submitted_at: null,
    last_receipt_at: null,
    created_at: "2026-09-02T15:00:00Z",
    updated_at: "2026-09-02T15:00:00Z",
    deadlines: NO_DEADLINE,
    next_action: "review_and_file",
    ...overrides,
  }
}
