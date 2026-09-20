// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The two dead ends: a session this form cannot use, and everything else.
 *
 * A session that expired and a session that never stepped up land on the
 * same screen. The shell owns both — a form mounted inside it can neither
 * refresh a session nor raise a step-up prompt — so the only honest thing to
 * say is how to get a working link.
 */

import { Button } from "@/components/ui/button"
import {
  EXPIRED_BODY,
  EXPIRED_HEADING,
  LOADING,
  RETRY_ACTION,
  RETRY_BODY,
  RETRY_HEADING,
} from "./intakeCopy"

export function IntakeExpired() {
  return (
    <section data-testid="intake-expired" className="py-4 text-center">
      <h2 className="text-base font-semibold text-neutral-900">{EXPIRED_HEADING}</h2>
      <p className="mt-2 text-sm text-neutral-600">{EXPIRED_BODY}</p>
    </section>
  )
}

export function IntakeLoadFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <section data-testid="intake-load-failed" className="py-4 text-center">
      <h2 className="text-base font-semibold text-neutral-900">{RETRY_HEADING}</h2>
      <p className="mt-2 text-sm text-neutral-600">{RETRY_BODY}</p>
      <Button className="mt-4" onClick={onRetry}>
        {RETRY_ACTION}
      </Button>
    </section>
  )
}

export function IntakeLoading() {
  return (
    <div data-testid="intake-loading" className="space-y-3 py-6" aria-busy="true">
      <p className="sr-only">{LOADING}</p>
      <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
      <div className="h-24 w-full animate-pulse rounded bg-neutral-200" aria-hidden="true" />
      <div className="h-10 w-full animate-pulse rounded bg-neutral-200" aria-hidden="true" />
    </div>
  )
}
