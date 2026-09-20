// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The dead ends and the waiting state.
 *
 * A session that expired and a session that never stepped up land on the
 * same screen. The shell owns both — a module mounted inside it can neither
 * refresh a session nor raise a step-up prompt — so the only honest thing to
 * say is how to get a working link.
 */

import { Button } from "@/components/ui/button"
import {
  ALREADY_SENT_BODY,
  ALREADY_SENT_HEADING,
  EXPIRED_BODY,
  EXPIRED_HEADING,
  LOADING,
  RECEIPT_CLOSE,
  RETRY_ACTION,
  RETRY_BODY,
  RETRY_HEADING,
} from "./formsCopy"

export function FormsExpired() {
  return (
    <section data-testid="forms-expired" className="py-4 text-center">
      <h2 className="text-base font-semibold text-neutral-900">{EXPIRED_HEADING}</h2>
      <p className="mt-2 text-sm text-neutral-600">{EXPIRED_BODY}</p>
    </section>
  )
}

export function FormsLoadFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <section data-testid="forms-load-failed" className="py-4 text-center">
      <h2 className="text-base font-semibold text-neutral-900">{RETRY_HEADING}</h2>
      <p className="mt-2 text-sm text-neutral-600">{RETRY_BODY}</p>
      <Button className="mt-4" onClick={onRetry}>
        {RETRY_ACTION}
      </Button>
    </section>
  )
}

/**
 * The form stopped being this patient's to change while they had it open.
 *
 * A 409, which on this surface means it was handed in or withdrawn — from
 * another tab, or by the practice. Says where the form went and what to do,
 * and offers no retry, because there is nothing a retry would change.
 */
export function FormsAlreadySent({ onClose }: { onClose: () => void }) {
  return (
    <section data-testid="forms-already-sent" className="py-4 text-center">
      <h2 className="text-base font-semibold text-neutral-900">{ALREADY_SENT_HEADING}</h2>
      <p className="mt-2 text-sm text-neutral-600">{ALREADY_SENT_BODY}</p>
      <Button variant="outline" className="mt-4" data-testid="forms-closed-close" onClick={onClose}>
        {RECEIPT_CLOSE}
      </Button>
    </section>
  )
}

export function FormsLoading() {
  return (
    <div data-testid="forms-loading" className="space-y-3 py-6" aria-busy="true">
      <p className="sr-only">{LOADING}</p>
      <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
      <div className="h-24 w-full animate-pulse rounded bg-neutral-200" aria-hidden="true" />
      <div className="h-10 w-full animate-pulse rounded bg-neutral-200" aria-hidden="true" />
    </div>
  )
}
