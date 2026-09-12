// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The service codes an outpatient therapy practice reaches for most often.
 *
 * These are suggestions and nothing more. The field they feed is free text,
 * nothing is validated against this list, and no code is ever chosen for a
 * clinician — not from the appointment's length, not from its name. The
 * psychotherapy codes do band by session length, which is exactly why a
 * therapist who bills a 50-minute session one way and her colleague another
 * both have to be able to say so themselves.
 *
 * Descriptions are written in plain English on purpose: the official CPT
 * descriptors are the AMA's copyrighted text, and these exist to help someone
 * recognise a code they already know, not to reproduce a code set.
 */
export interface ServiceCodeSuggestion {
  code: string
  description: string
}

export const COMMON_SERVICE_CODES: ServiceCodeSuggestion[] = [
  { code: "90791", description: "First visit — diagnostic evaluation" },
  { code: "90792", description: "First visit — evaluation with medical services" },
  { code: "90832", description: "Therapy session — around 30 minutes" },
  { code: "90834", description: "Therapy session — around 45 minutes" },
  { code: "90837", description: "Therapy session — 60 minutes or more" },
  { code: "90846", description: "Family session — patient not present" },
  { code: "90847", description: "Family or couples session — patient present" },
  { code: "90853", description: "Group session" },
  { code: "90839", description: "Crisis session — first 60 minutes" },
]

/** The plain-English name for a code, when it is one we happen to know. */
export function describeServiceCode(code: string | null | undefined): string | null {
  if (!code) return null
  const match = COMMON_SERVICE_CODES.find((c) => c.code === code.trim().toUpperCase())
  return match ? match.description : null
}

/** Tidy a hand-typed code the way the API will. Mirrors `normalize_service_code`. */
export function normalizeServiceCode(raw: string): string | null {
  return raw.trim().toUpperCase() || null
}
