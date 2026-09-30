// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The zone the browser runs in, and the one a spec must format expected
 * dates in.
 *
 * The stack runs in UTC and so does the Node process running the specs, so a
 * spec that formats "today" with a bare `toLocaleDateString()` gets UTC's
 * date. The page shows the browser's date. For four hours every evening
 * those are different days. Format with this zone instead.
 */
export const BROWSER_TIME_ZONE = "America/New_York"
