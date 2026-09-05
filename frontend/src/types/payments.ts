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
  /** Omit to charge the client's resolved rate. */
  amount_cents?: number
  appointment_id?: string
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
  created_at: string
  updated_at: string | null
}
