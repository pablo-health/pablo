// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ConfirmationSource, ChecklistField, ChecklistTier } from "@/types/credentialing"

/**
 * How each tier is named and framed to the clinician.
 *
 * The wording is load-bearing, and it has to work for two customers at once.
 * Someone who came for credentialing may never bill through Pablo; someone
 * who came to bill may never apply to a panel. Tier 1 is the overlap — every
 * field in it is on a payer application AND needed to get paid — so its copy
 * names both reasons and leans on neither. Tier 2 must read as optional
 * without reading as unimportant.
 */
export interface TierCopy {
  id: ChecklistTier
  /** Step label. Never a number alone — "Step 2 of 3" implies three are owed. */
  label: string
  blurb: string
}

export const TIERS: TierCopy[] = [
  {
    id: "tier_0_confirm",
    label: "Check what we know",
    blurb:
      "We looked these up. Tell us if anything is wrong — there is nothing to type.",
  },
  {
    id: "tier_1_claims_ready",
    label: "About your practice",
    blurb:
      "Every payer application asks for all of this, and so does getting you paid. Answer it once and it counts for both.",
  },
  {
    id: "tier_2_credentialing",
    label: "Applying to panels",
    blurb:
      "Only needed if you want to join insurance networks. You can leave this for another day.",
  },
]

/** What we say about where a pre-filled value came from. */
export const SOURCE_LABELS: Record<ConfirmationSource, string> = {
  nppes: "the NPPES registry",
  pecos_public_file: "the public Medicare enrolment file",
  leie_sam: "the federal exclusion lists",
  clinician_profiles: "your profile",
  practice_billing_profile: "your practice details",
}

export function sourceLabel(source: ConfirmationSource | null): string {
  return source ? SOURCE_LABELS[source] : "your record"
}

/** Human-readable CAQH section headings, keyed by the API's section value. */
export const SECTION_LABELS: Record<string, string> = {
  personal_information: "About you",
  professional_ids: "Licences and identifiers",
  education_and_professional_training: "Education and training",
  specialties: "Specialties",
  practice_locations: "Where you practise",
  hospital_affiliations: "Hospital affiliations",
  credential_contacts: "Who payers should contact",
  professional_liability_insurance: "Malpractice cover",
  employment_information: "Work history",
  professional_references: "References",
  disclosure: "Disclosure questions",
}

export function sectionLabel(section: string): string {
  return SECTION_LABELS[section] ?? section
}

/** Fields of one tier, in the order the server gave them. */
export function fieldsForTier(fields: ChecklistField[], tier: ChecklistTier): ChecklistField[] {
  return fields.filter((f) => f.tier === tier)
}

/**
 * Group a tier's fields under their CAQH section, preserving server order for
 * both the sections and the fields inside them.
 *
 * Order is not ours to choose: it is the order the provider data portal's
 * profile uses, which is what lets the collected record export into it rather
 * than needing translation.
 */
export function groupBySection(fields: ChecklistField[]): [string, ChecklistField[]][] {
  const groups = new Map<string, ChecklistField[]>()
  for (const field of fields) {
    const existing = groups.get(field.section)
    if (existing) existing.push(field)
    else groups.set(field.section, [field])
  }
  return [...groups.entries()]
}
