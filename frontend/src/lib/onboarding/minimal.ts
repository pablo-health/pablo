// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The default (minimal) onboarding surface.
 *
 * A stock deployment doesn't run a guided setup wizard — the only thing
 * onboarding must guarantee is that every account has a second factor
 * before it reaches the dashboard. So this surface holds a single
 * required step: passkey enrolment, present only when passkeys are
 * enabled for the deployment (`PASSKEYS_ENABLED`).
 *
 * When passkeys are disabled the surface is empty: nothing is required,
 * `firstIncompleteRequiredStep` returns null, and the dashboard layout
 * falls through to its standalone `/mfa-enrollment` (TOTP) safety-net
 * exactly as before this surface existed.
 *
 * The `/onboarding/mfa` (authenticator-app) page is a reachable
 * fallback route but is intentionally NOT a member of this surface: the
 * passkey step covers `mfa_enrolled_at`, and a deployment whose auth
 * backend can't enrol TOTP shouldn't advertise it. The passkey page
 * only links to the TOTP fallback when the active surface actually
 * contains an `mfa` step.
 *
 * A downstream build supplies its own richer surface and selects it in
 * a shadowed ./surface.ts.
 *
 * The surface ends with an optional working-hours step: it never blocks
 * the dashboard (`required: false`), and its gate is the generic
 * `onboarding_state` field the backend already exposes, set to
 * "completed" whether the user saves a schedule or skips the step.
 */

import type { OnboardingSurface, StepDef } from "./types"

const PASSKEYS_ENABLED = process.env.PASSKEYS_ENABLED === "true"

// No `group` here: grouping only matters when it shares a step number
// with a sibling (a passkey/TOTP pair in a richer surface). As the lone
// required step it stands alone.
const PASSKEY_STEP: StepDef = {
  id: "passkey",
  path: "/onboarding/passkey",
  gate: (status) => Boolean(status.mfa_enrolled_at),
}

const SCHEDULE_STEP: StepDef = {
  id: "schedule",
  path: "/onboarding/schedule",
  gate: (status) => status.onboarding_state === "completed",
  required: false,
}

export const MINIMAL_ONBOARDING_SURFACE: OnboardingSurface = {
  steps: [...(PASSKEYS_ENABLED ? [PASSKEY_STEP] : []), SCHEDULE_STEP],
}
