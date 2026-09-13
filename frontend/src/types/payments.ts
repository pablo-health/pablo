// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Self-pay card payment API types.
 *
 * Field names are snake_case to match `app.models.payments` exactly.
 *
 * Nothing here carries a card number, and there is no type that could: the
 * browser posts the card straight to Stripe against a SetupIntent, and the
 * only card-shaped values that come back are the brand, last four digits and
 * expiry the chart renders.
 */

/** The card a practice has on file, as the UI renders it. */
export interface CardOnFileResponse {
  brand: string | null
  last4: string | null
  exp_month: number | null
  exp_year: number | null
  /** False for a setup that was started and never confirmed. */
  chargeable: boolean
}

/**
 * Everything Stripe.js has to be initialised with, from one call.
 *
 * `stripe_account_id` is present only on a deployment whose credentials name
 * an account; it must be passed as `stripeAccount` when it is there and left
 * off entirely when it is not.
 */
export interface CardSetupResponse {
  client_secret: string
  publishable_key: string
  stripe_account_id: string | null
}

/** What a charge sent without an explicit amount would come to. */
export interface ChargeAmountResponse {
  /** `null` when no rate is set — unknown, not free. */
  amount_cents: number | null
  currency: string
}

export interface CreateChargeRequest {
  /** Omit to let the server resolve the amount from `kind`. */
  amount_cents?: number
  appointment_id?: string
  /** Defaults to `session` server-side. */
  kind?: ChargeableKind
}

/**
 * One row of the charge ledger.
 *
 * A decline is a `failed` row returned with HTTP 200, not an error: the
 * attempt happened, and `status_detail` carries the processor's reason.
 */
export interface ChargeResponse {
  id: string
  amount_cents: number
  currency: string
  status: string
  status_detail: string | null
  appointment_id: string | null
  /** What the row IS — see `app.db.models.CHARGE_KINDS`. */
  kind: ChargeKind
  /** The claim a remittance wrote this row out of; `null` otherwise. */
  claim_id: string | null
  write_off_reason: string | null
  note: string | null
  /** Which charge paid this bill off. Provenance, not arithmetic. */
  settled_by_charge_id: string | null
  created_at: string
  updated_at: string | null
}

/**
 * The kinds of thing a ledger row can be.
 *
 * `session` is the practice's own charge for a visit — both the bill and its
 * own payment attempt. `payment` collects against a bill somebody else
 * raised, which a `session` row cannot do without re-billing the amount it
 * settles.
 */
export type ChargeKind =
  | "session"
  | "copay"
  | "payment"
  | "patient_resp"
  | "contractual_adjustment"
  | "write_off"
  | "credit"

/**
 * The two things a card is charged for: the visit at its full rate, and the
 * copay a covered client pays at the door. The rest of the ledger kinds move
 * no money on their own and cannot be raised through the charge route —
 * mirroring the request model's ``Literal["session", "copay"]`` server-side.
 *
 * Derived from `ChargeKind` rather than spelled out again, so a kind that is
 * removed from the ledger cannot survive here as something still chargeable.
 */
export type ChargeableKind = Extract<ChargeKind, "session" | "copay">

/**
 * Why a practice stopped trying to collect. Mirrors
 * `app.db.models.WRITE_OFF_REASONS` — the fixed set the CHECK constraint
 * enforces. `courtesy` and `small_balance` are further gated by practice
 * policy on the billing profile; `hardship` and `error` are not.
 */
export type WriteOffReason = "hardship" | "small_balance" | "courtesy" | "error"

/** A practice-initiated write-off: money it has decided not to collect. */
export interface CreateWriteOffRequest {
  amount_cents: number
  reason: WriteOffReason
  note?: string
}

/** One visit's line of the balance. */
export interface VisitBalanceResponse {
  /** `null` for the rows that hang off no visit, on one trailing line. */
  appointment_id: string | null
  owed_cents: number
  collected_cents: number
  written_off_cents: number
  adjusted_cents: number
  credited_cents: number
  balance_cents: number
}

/**
 * What a client owes, and the arithmetic behind it.
 *
 * `balance_cents` is positive when the client owes the practice and negative
 * when the practice owes the client. A credit is not clamped to zero: a
 * refund the practice owes is exactly what clamping would hide.
 *
 * `outcome_known` is false when this client's payer sends its remittances
 * somewhere other than Pablo. The arithmetic is still right over the rows we
 * have; what it cannot include is the client's share of an insured visit,
 * which only an 835 tells us. Rendering the total without saying so states a
 * settled account that was never settled.
 */
export interface BalanceResponse {
  owed_cents: number
  collected_cents: number
  written_off_cents: number
  adjusted_cents: number
  credited_cents: number
  balance_cents: number
  outcome_known: boolean
  by_visit: VisitBalanceResponse[]
}

/** One client on the practice-wide balances list. */
export interface ClientBalanceItem {
  patient_id: string
  patient_name: string
  balance_cents: number
  currency: string
  /** When the money behind the balance first went on the ledger. */
  outstanding_since: string
  /**
   * False when this client's payer sends its remittances elsewhere, so what
   * is owed is at least this and possibly more.
   */
  outcome_known: boolean
}

export interface BalancesResponse {
  items: ClientBalanceItem[]
}
