// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type React from "react"
import { Label } from "@/components/ui/label"
import type { BillingProfileResponse, UpdateBillingProfileRequest } from "@/types/practiceBilling"

/**
 * What the practice-identity and billing-contact cards both need.
 *
 * The two are separate components on purpose — each owns its own draft and its
 * own Save, so a therapist can do one and come back for the other. This is
 * only the machinery they genuinely share: how a field looks, and how a patch
 * is built from the fields a card actually owns.
 *
 * Each card sends ONLY its own fields. The endpoint merges with
 * ``exclude_unset``, so a half-filled profile saved in two sittings ends up the
 * same as one saved in a single pass, and neither card can blank the other's
 * work by omitting it.
 */

export type TextField =
  | "legal_name"
  | "billing_npi"
  | "address_line1"
  | "address_line2"
  | "city"
  | "state"
  | "postal_code"
  | "phone"
  | "contact_email"

/** What the clinician's profile knows about the practice, as a starting point. */
export interface PracticeDetails {
  name?: string | null
  phone?: string | null
  /** One free-text line, as the professional-info step captured it. */
  address?: string | null
}

/**
 * Only what changed, among the fields this card owns.
 *
 * ``legal_name`` is never blanked: clearing it would take the practice's
 * identity off a claim, and an empty box is far more likely to be a mis-click
 * than a decision.
 */
export function textPatch(
  profile: BillingProfileResponse,
  draft: Partial<Record<TextField, string>>,
  fields: readonly TextField[],
): UpdateBillingProfileRequest {
  const patch: UpdateBillingProfileRequest = {}
  for (const field of fields) {
    const value = draft[field]
    if (value === undefined) continue
    const next = value.trim()
    if (next === (profile[field] ?? "")) continue
    if (field === "legal_name") {
      if (next) patch.legal_name = next
    } else {
      patch[field] = next || null
    }
  }
  return patch
}

export function Field({
  id,
  label,
  help,
  children,
}: {
  id: string
  label: string
  help?: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help && <p className="text-[12.5px] text-muted-foreground">{help}</p>}
    </div>
  )
}
