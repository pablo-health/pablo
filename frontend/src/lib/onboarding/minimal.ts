// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The default (minimal) onboarding surface.
 *
 * A stock deployment doesn't run a guided setup wizard — the only thing
 * onboarding must guarantee is that every account has a second factor
 * before it reaches the dashboard. So this surface's one required step
 * is the second factor: passkey enrolment, present only when passkeys
 * are enabled for the deployment (`PASSKEYS_ENABLED`).
 *
 * When neither passkeys nor TOTP are enabled nothing is required,
 * `firstIncompleteRequiredStep` returns null, and the dashboard layout
 * falls through to its standalone `/mfa-enrollment` (TOTP) safety-net
 * exactly as before this surface existed.
 *
 * The `/onboarding/mfa` (authenticator-app) step joins the surface only
 * when the deployment says its auth backend can enrol TOTP
 * (`TOTP_ENABLED`); one that can't shouldn't advertise it. The passkey
 * page links to it only when the surface contains it, which matters on a
 * device that cannot create a passkey. Beside a passkey step the two are
 * grouped: both gate on `mfa_enrolled_at`, so finishing either clears
 * both, and they count as one step.
 *
 * A downstream build supplies its own richer surface and selects it in
 * a shadowed ./surface.ts.
 *
 * After the second factor comes an optional import question — "are you
 * importing from another records system?" — asked once, gated on
 * `import_prompted_at`, and leading to the import screen for a practice that
 * names one.
 *
 * The surface ends with an optional working-hours step: it never blocks
 * the dashboard (`required: false`), and its gate is the generic
 * `onboarding_state` field the backend already exposes, set to
 * "completed" whether the user saves a schedule or skips the step.
 */

import type { OnboardingSurface, StepDef } from "./types"

// Read by the Next server at run time, so one prebuilt image serves
// deployments that differ.
const PASSKEYS_ENABLED = process.env.PASSKEYS_ENABLED === "true"
const TOTP_ENABLED = process.env.TOTP_ENABLED === "true"

const SECOND_FACTOR_GROUP = "second-factor"

const hasSecondFactor: StepDef["gate"] = (status) => Boolean(status.mfa_enrolled_at)

/**
 * The second-factor steps: passkey first, the authenticator app as its
 * alternative. A lone step carries no `group`, so it numbers plainly
 * rather than as a lettered sub-step with no sibling.
 */
function secondFactorSteps(passkeys: boolean, totp: boolean): StepDef[] {
  const group = passkeys && totp ? { group: SECOND_FACTOR_GROUP } : {}
  const passkey: StepDef = { id: "passkey", path: "/onboarding/passkey", gate: hasSecondFactor, ...group }
  const authenticatorApp: StepDef = { id: "mfa", path: "/onboarding/mfa", gate: hasSecondFactor, ...group }
  return [...(passkeys ? [passkey] : []), ...(totp ? [authenticatorApp] : [])]
}

// Optional and asked once: "Are you importing from another records system?"
// Answering (or skipping) stamps import_prompted_at, which is the gate. A
// downstream surface can reuse this step as it is — its id, path and gate are
// the contract — and place it wherever its own flow wants it.
export const IMPORT_SOURCE_STEP: StepDef = {
  id: "import-source",
  path: "/onboarding/import-source",
  gate: (status) => Boolean(status.import_prompted_at),
  required: false,
}

const SCHEDULE_STEP: StepDef = {
  id: "schedule",
  path: "/onboarding/schedule",
  gate: (status) => status.onboarding_state === "completed",
  required: false,
}

/** The stock surface for a deployment with these second factors enabled. */
export function minimalOnboardingSurface({
  passkeys,
  totp,
}: {
  passkeys: boolean
  totp: boolean
}): OnboardingSurface {
  return {
    steps: [...secondFactorSteps(passkeys, totp), IMPORT_SOURCE_STEP, SCHEDULE_STEP],
  }
}

export const MINIMAL_ONBOARDING_SURFACE: OnboardingSurface = minimalOnboardingSurface({
  passkeys: PASSKEYS_ENABLED,
  totp: TOTP_ENABLED,
})
