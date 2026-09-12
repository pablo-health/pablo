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
