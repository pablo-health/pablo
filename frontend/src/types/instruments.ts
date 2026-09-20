// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The instruments the engine knows, and what this practice may do with each.
 *
 * One list serves two screens. The settings section shows the restricted
 * ones so a practice can record the permission it holds; the form builder
 * shows which measures a question may ask and which are waiting on that
 * permission. They read the same rows so they cannot disagree about which
 * instruments exist.
 */

/**
 * What may be done with an instrument's wording.
 *
 * `public_domain` — offered everywhere, nothing to record.
 * `attestation_required` — the wording may be reproduced, the scale's use is
 * restricted, and the practice records the permission it holds.
 * `never_ship` — the form is a sold product. The engine carries the name and
 * the item count so a practice can recognise it, and never the questions.
 */
export const INSTRUMENT_RIGHTS = [
  "public_domain",
  "attestation_required",
  "never_ship",
] as const

export type InstrumentRights = (typeof INSTRUMENT_RIGHTS)[number]

export interface Instrument {
  code: string
  display_name: string
  rights: InstrumentRights
  /** One line saying what the restriction actually is. */
  rights_note: string
  /** Where to read the publisher's own terms, where there is such a page. */
  publisher_url: string | null
  item_count: number
  /** Whether the engine carries this instrument's questions at all. */
  can_ask_on_a_form: boolean
  /** Whether this practice has recorded the permission it requires. */
  attested: boolean
  attested_at: string | null
  license_reference: string | null
}

export interface AttestInstrumentInput {
  license_reference?: string | null
  notes?: string | null
}

export interface InstrumentAttestation {
  id: string
  instrument_code: string
  attested_at: string
  license_reference: string | null
  notes: string | null
  revoked_at: string | null
}
