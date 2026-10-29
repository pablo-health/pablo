// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings URL segments other surfaces link to. Kept apart from the registry
 * so a link from elsewhere in the app does not pull every settings page into
 * its bundle.
 */

/**
 * How insurers identify the practice; claim review links here on a missing
 * profile field. The id is unchanged from when this page held the whole
 * billing profile, so links stored elsewhere keep working.
 */
export const BILLING_PROFILE_SETTINGS_ID = "billing-profile"
export const BILLING_PROFILE_SETTINGS_PATH = `/dashboard/settings/${BILLING_PROFILE_SETTINGS_ID}`

/** Where insurers reach the practice. One settings item per wizard step. */
export const BILLING_CONTACT_SETTINGS_ID = "billing-contact"
export const BILLING_CONTACT_SETTINGS_PATH = `/dashboard/settings/${BILLING_CONTACT_SETTINGS_ID}`

/** The payer list; the claims setup checklist links here for the first payer. */
export const INSURANCE_PAYERS_SETTINGS_ID = "insurance"
export const INSURANCE_PAYERS_SETTINGS_PATH = `/dashboard/settings/${INSURANCE_PAYERS_SETTINGS_ID}`

/**
 * Where credentialing starts: the NPI lookup, and what it finds.
 * The billing setup wizard mounts the same screen as its first record step.
 */
export const CREDENTIALING_SETTINGS_ID = "credentialing"
/**
 * The feature that offers credentialing. Applications are prepared and filed
 * by people behind the service, so a deployment without that service (a
 * self-hosted install) leaves it off and shows no credentialing at all, rather
 * than a lookup that leads to a promise nothing keeps.
 */
export const CREDENTIALING_FEATURE = "credentialing"
export const CREDENTIALING_SETTINGS_PATH = `/dashboard/settings/${CREDENTIALING_SETTINGS_ID}`

/**
 * The full tiered checklist, being replaced by the flow in front of it. Kept
 * rather than deleted: it is the only surface that reaches Tier 1 and Tier 2
 * today. Behind its own feature, which no deployment turns on, so clinicians
 * no longer see it; a deployment that needs those tiers meanwhile can.
 */
export const CREDENTIALING_LEGACY_SETTINGS_ID = "credentialing-deprecated"
export const CREDENTIALING_LEGACY_FEATURE = "credentialing_checklist"
export const CREDENTIALING_LEGACY_SETTINGS_PATH = `/dashboard/settings/${CREDENTIALING_LEGACY_SETTINGS_ID}`

/** Whether the practice offers the portal; Settings > Domains links here while it does not. */
export const PATIENT_PORTAL_SETTINGS_ID = "portal"
export const PATIENT_PORTAL_SETTINGS_PATH = `/dashboard/settings/${PATIENT_PORTAL_SETTINGS_ID}`

/** The clinician's waitlist; a client page on it links here. */
export const WAITLIST_SETTINGS_ID = "waitlist"
export const WAITLIST_SETTINGS_PATH = `/dashboard/settings/${WAITLIST_SETTINGS_ID}`

/** The practice's own domains; Settings > Website links here until a website domain works. */
export const DOMAINS_SETTINGS_ID = "domains"
export const DOMAINS_SETTINGS_PATH = `/dashboard/settings/${DOMAINS_SETTINGS_ID}`
