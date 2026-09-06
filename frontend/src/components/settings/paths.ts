// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings URL segments other surfaces link to. Kept apart from the registry
 * so a link from elsewhere in the app does not pull every settings page into
 * its bundle.
 */

/** The practice billing profile; claim review links here on a missing profile field. */
export const BILLING_PROFILE_SETTINGS_ID = "billing-profile"
export const BILLING_PROFILE_SETTINGS_PATH = `/dashboard/settings/${BILLING_PROFILE_SETTINGS_ID}`
