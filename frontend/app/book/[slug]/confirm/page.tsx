// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Suspense, useEffect, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { buildApiUrl } from "@/lib/api/client"
import { BookingConfirmedCard, type Confirmation } from "@/components/booking/BookingConfirmedCard"

/**
 * Confirms an email-verified booking hold (docs/design/public-booking.md).
 *
 * Fires the confirm POST on mount rather than behind a button: the token
 * in the query string is a one-time credential, and a GET here is exactly
 * what a mail scanner or link previewer fetches before a person ever
 * clicks — see the docstring on the backend confirm route. Sending the
 * request as a POST keeps it out of a scanner's reach entirely.
 */

const _CONFIRMATION_INVALID = "This confirmation link is not valid."
const _SLOT_TAKEN = "That time was taken while you were confirming. Please pick another slot."

type ConfirmState =
  | { kind: "loading" }
  | { kind: "success"; confirmation: Confirmation }
  | { kind: "invalid" }
  | { kind: "slot-taken" }
  | { kind: "network-error" }

function ConfirmBookingInner({ slug }: { slug: string }) {
  const searchParams = useSearchParams()
  const token = searchParams.get("token")
  const [state, setState] = useState<ConfirmState>({ kind: "loading" })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!token) return
    let active = true
    void (async () => {
      try {
        const resp = await fetch(buildApiUrl(`/api/public/booking-links/${slug}/confirm`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        })
        if (!active) return
        if (resp.status === 404) {
          setState({ kind: "invalid" })
          return
        }
        if (resp.status === 409) {
          setState({ kind: "slot-taken" })
          return
        }
        if (!resp.ok) {
          setState({ kind: "network-error" })
          return
        }
        const confirmation = (await resp.json()) as Confirmation
        setState({ kind: "success", confirmation })
      } catch {
        if (active) setState({ kind: "network-error" })
      }
    })()
    return () => {
      active = false
    }
  }, [slug, token, attempt])

  if (!token) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center">
        <h1 className="text-2xl font-display font-bold text-neutral-900">
          This confirmation link isn&apos;t valid
        </h1>
        <p className="max-w-md text-muted-foreground">{_CONFIRMATION_INVALID}</p>
      </main>
    )
  }

  if (state.kind === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-muted-foreground">Confirming your booking…</p>
      </main>
    )
  }

  if (state.kind === "success") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-12">
        <BookingConfirmedCard confirmation={state.confirmation} />
      </main>
    )
  }

  if (state.kind === "invalid") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center">
        <h1 className="text-2xl font-display font-bold text-neutral-900">
          This confirmation link isn&apos;t valid
        </h1>
        <p className="max-w-md text-muted-foreground">{_CONFIRMATION_INVALID}</p>
      </main>
    )
  }

  if (state.kind === "slot-taken") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center">
        <h1 className="text-2xl font-display font-bold text-neutral-900">That time is gone</h1>
        <p className="max-w-md text-muted-foreground">{_SLOT_TAKEN}</p>
        <Link href={`/book/${slug}`} className="text-primary-700 underline">
          Pick another time
        </Link>
      </main>
    )
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center">
      <h1 className="text-2xl font-display font-bold text-neutral-900">Something went wrong</h1>
      <p className="max-w-md text-muted-foreground">
        Network error. Please check your connection and try again.
      </p>
      <Button onClick={() => setAttempt((n) => n + 1)}>Retry</Button>
    </main>
  )
}

export default function ConfirmBookingPage({
  params,
}: {
  params: Promise<{ slug: string }>
}) {
  const [slug, setSlug] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    void params.then(({ slug }) => {
      if (active) setSlug(slug)
    })
    return () => {
      active = false
    }
  }, [params])

  if (slug === null) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-muted-foreground">Loading…</p>
      </main>
    )
  }

  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center bg-background">
          <p className="text-muted-foreground">Confirming your booking…</p>
        </main>
      }
    >
      <ConfirmBookingInner slug={slug} />
    </Suspense>
  )
}
