// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Display helpers for the card on file and the charge ledger.
 *
 * The backend deliberately stores the processor's own token on a failed charge
 * and writes no copy of its own, so turning a token into a sentence is this
 * side's job and it happens once, here.
 */

import type { CardOnFileResponse, ChargeResponse } from "@/types/payments"

/** `visa` -> `Visa`, `amex` -> `Amex`; unknown brands pass through capitalised. */
export function formatCardBrand(brand: string | null): string {
  if (!brand) return "Card"
  return brand.charAt(0).toUpperCase() + brand.slice(1)
}

/** `Visa •••• 4242` — the whole of what is knowable about a stored card. */
export function formatCard(card: CardOnFileResponse): string {
  return `${formatCardBrand(card.brand)} •••• ${card.last4 ?? "????"}`
}

/** `04/2030`, or an em dash when the processor reported no expiry. */
export function formatCardExpiry(card: CardOnFileResponse): string {
  if (!card.exp_month || !card.exp_year) return "—"
  return `${String(card.exp_month).padStart(2, "0")}/${card.exp_year}`
}

export interface ChargeStatusBadge {
  label: string
  className: string
}

export function chargeStatusBadge(status: string): ChargeStatusBadge {
  switch (status) {
    case "succeeded":
      return { label: "Paid", className: "bg-secondary-100 text-secondary-700" }
    case "failed":
      return { label: "Declined", className: "bg-red-100 text-red-800" }
    default:
      return { label: "Pending", className: "bg-yellow-100 text-yellow-800" }
  }
}

/**
 * The processor's decline codes, in words.
 *
 * Only the codes a clinician can act on differently are spelled out; anything
 * else falls back to the raw token rather than a reassuring guess, because
 * showing the code lets somebody look it up and inventing copy does not. The
 * fallback also covers the case where the processor gave no code at all and
 * the backend recorded the intent's own status instead.
 */
const DECLINE_REASONS: Record<string, string> = {
  insufficient_funds: "The card has insufficient funds.",
  card_declined: "The card was declined.",
  generic_decline: "The card was declined.",
  expired_card: "The card has expired.",
  incorrect_cvc: "The card's security code was rejected.",
  processing_error: "The processor had an error. Trying again may work.",
  lost_card: "The card was reported lost.",
  stolen_card: "The card was reported stolen.",
  do_not_honor: "The bank declined the charge without giving a reason.",
  authentication_required:
    "The bank wants the client to confirm this charge, which a saved card cannot do. Ask them to pay another way.",
  requires_action:
    "The bank wants the client to confirm this charge, which a saved card cannot do. Ask them to pay another way.",
}

export function declineReason(charge: ChargeResponse): string {
  if (!charge.status_detail) return "The card was declined."
  return DECLINE_REASONS[charge.status_detail] ?? `The card was declined (${charge.status_detail}).`
}

export function formatChargeDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}
