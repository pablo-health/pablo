// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The passkey sign-in ceremony, with a time limit on every step.
 *
 * A passkey sign-in is four awaited steps:
 *
 *   1. "challenge": fetch the WebAuthn options from the API.
 *   2. "webauthn":  the browser's passkey prompt (a person is involved).
 *   3. "verify":    the API checks the assertion and returns a custom token.
 *   4. "sign-in":   the caller exchanges that token for a Firebase session and
 *                   hands the credential to the host.
 *
 * Without a limit, a step that never settles leaves the button spinning
 * forever. That has been seen in a browser profile with many tabs of the app
 * open while a private window signed in fine, which points at state the tabs
 * share: Firebase Auth persists to IndexedDB and syncs it across tabs (steps
 * 1, 3 and 4 all read the current user), and a WebAuthn request can sit behind
 * one pending elsewhere (step 2). Rather than guess which, each step reports
 * its own name when it runs out of time, so the next occurrence says where it
 * stalled.
 *
 * Steps 1, 3 and 4 have a fixed limit. Step 2 waits for a person, so its limit
 * is the ceremony timeout the server put in the options (the browser enforces
 * that one itself) plus some grace; it only fires when the browser failed to
 * end the request on its own.
 */

import {
  startAuthentication,
  WebAuthnAbortService,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser"
import { beginAuthentication, finishAuthentication } from "@/lib/api/passkey"
import { reportFrontendError } from "@/lib/feErrorReporter"

export type PasskeyStep = "challenge" | "webauthn" | "verify" | "sign-in"

/** Steps 1 and 3: one API round trip each. */
export const PASSKEY_API_STEP_LIMIT_MS = 15_000
/** Step 4: the Firebase token exchange plus the host's own session setup. */
export const PASSKEY_SIGN_IN_STEP_LIMIT_MS = 20_000
/** The WebAuthn spec's suggested default, for options that carry no timeout. */
export const WEBAUTHN_DEFAULT_TIMEOUT_MS = 60_000
/** How long past the ceremony's own timeout before we give up on the browser. */
export const WEBAUTHN_GRACE_MS = 15_000

export class PasskeyStepTimeoutError extends Error {
  readonly step: PasskeyStep
  readonly elapsedMs: number

  constructor(step: PasskeyStep, elapsedMs: number) {
    super(`passkey step "${step}" did not finish within ${elapsedMs} ms`)
    this.name = "PasskeyStepTimeoutError"
    this.step = step
    this.elapsedMs = elapsedMs
  }
}

/**
 * Run `work`, rejecting with a `PasskeyStepTimeoutError` naming `step` if it
 * has not settled within `limitMs`. The work itself is not cancelled (a
 * promise can't be); the caller just stops waiting for it.
 */
export async function withStepTimeout<T>(
  step: PasskeyStep,
  limitMs: number,
  work: () => Promise<T>,
): Promise<T> {
  const started = Date.now()
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new PasskeyStepTimeoutError(step, Date.now() - started)),
      limitMs,
    )
  })
  try {
    return await Promise.race([work(), timeout])
  } finally {
    clearTimeout(timer)
  }
}

export function webauthnStepLimitMs(options: PublicKeyCredentialRequestOptionsJSON): number {
  const ceremony =
    typeof options.timeout === "number" && options.timeout > 0
      ? options.timeout
      : WEBAUTHN_DEFAULT_TIMEOUT_MS
  return ceremony + WEBAUTHN_GRACE_MS
}

/**
 * Run the four steps. `signIn` is step 4: given the custom token, it signs in
 * and hands the credential on, and the whole of it shares one limit.
 */
export async function runPasskeyCeremony(
  signIn: (customToken: string) => Promise<void>,
): Promise<void> {
  const options = await withStepTimeout("challenge", PASSKEY_API_STEP_LIMIT_MS, () =>
    beginAuthentication(),
  )
  let assertion
  try {
    assertion = await withStepTimeout("webauthn", webauthnStepLimitMs(options), () =>
      startAuthentication({ optionsJSON: options }),
    )
  } catch (err) {
    // Release the request the browser never ended, so it can't hold up the
    // next attempt.
    if (err instanceof PasskeyStepTimeoutError) WebAuthnAbortService.cancelCeremony()
    throw err
  }
  const { custom_token } = await withStepTimeout("verify", PASSKEY_API_STEP_LIMIT_MS, () =>
    finishAuthentication(assertion),
  )
  await withStepTimeout("sign-in", PASSKEY_SIGN_IN_STEP_LIMIT_MS, () => signIn(custom_token))
}

/**
 * Record which step stalled, through the frontend error reporter. Carries the
 * flow, the step and the elapsed time — nothing about the person signing in.
 * Also written to the console, which is where it is visible when the
 * reporter's endpoint refuses a browser that is not signed in yet.
 */
export function reportPasskeyStall(
  flow: "sign-in" | "step-up",
  err: PasskeyStepTimeoutError,
): void {
  const detail = `passkey ${flow} step=${err.step} elapsed_ms=${err.elapsedMs}`
  console.warn(`Passkey sign-in stalled: ${detail}`)
  const report = new Error(detail)
  // The reporter ships the error's name and the top stack frame, never the
  // message, so both carry the step.
  report.name = `PasskeyStepTimeout:${err.step}`
  report.stack = `${report.name}\n    at ${detail}`
  void reportFrontendError(report)
}
