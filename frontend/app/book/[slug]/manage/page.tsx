// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Suspense, useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { buildApiUrl } from "@/lib/api/client"
import { longDateLabel, slotTimeLabel } from "@/lib/booking/time"

/**
 * Where a booker's manage link (docs/design/public-booking.md) lands.
 *
 * The token in the query string is the same confirmation token from the
 * booking email — a capability, not an id — folded into this URL. POSTs
 * it on mount to look up the booking, and POSTs again (behind an explicit
 * confirm step) to cancel it. Every failure — an unknown token, another
 * link's token, an already-cancelled booking, or one whose start time has
 * passed — renders the same generic "not valid" copy; the backend gives
 * no way to tell those apart and the frontend must not invent one.
 */

const _LINK_INVALID = "This link is not valid or has expired."

interface ManagedBooking {
  title: string
  host_name: string
  start_at: string
  end_at: string
  duration_minutes: number
  status: string
}

type ManageState =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "ready"; booking: ManagedBooking; confirming: boolean; cancelling: boolean; error: string | null }
  | { kind: "cancelled"; booking: ManagedBooking }

function ManageBookingInner({ slug }: { slug: string }) {
  const searchParams = useSearchParams()
  const token = searchParams.get("token")
  const [state, setState] = useState<ManageState>({ kind: "loading" })

  useEffect(() => {
    if (!token) return
    let active = true
    void (async () => {
      try {
        const resp = await fetch(buildApiUrl(`/api/public/booking-links/${slug}/manage`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        })
        if (!active) return
        if (!resp.ok) {
          setState({ kind: "invalid" })
          return
        }
        const booking = (await resp.json()) as ManagedBooking
        setState({ kind: "ready", booking, confirming: false, cancelling: false, error: null })
      } catch {
        if (active) setState({ kind: "invalid" })
      }
    })()
    return () => {
      active = false
    }
  }, [slug, token])

  async function cancelBooking() {
    if (state.kind !== "ready" || !token) return
    const booking = state.booking
    setState({ ...state, cancelling: true, error: null })
    try {
      const resp = await fetch(buildApiUrl(`/api/public/booking-links/${slug}/manage/cancel`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      })
      if (!resp.ok) {
        setState({
          kind: "ready",
          booking,
          confirming: true,
          cancelling: false,
          error: "Something went wrong. Please try again.",
        })
        return
      }
      setState({ kind: "cancelled", booking })
    } catch {
      setState({
        kind: "ready",
        booking,
        confirming: true,
        cancelling: false,
        error: "Network error. Please check your connection and try again.",
      })
    }
  }

  if (!token || state.kind === "invalid") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center">
        <h1 className="text-2xl font-display font-bold text-neutral-900">
          This link isn&apos;t valid
        </h1>
        <p className="max-w-md text-muted-foreground">{_LINK_INVALID}</p>
      </main>
    )
  }

  if (state.kind === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-muted-foreground">Loading your booking…</p>
      </main>
    )
  }

  if (state.kind === "cancelled") {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-12">
        <div className="card w-full max-w-md text-center">
          <h1 className="mb-2 text-2xl font-display font-bold text-neutral-900">
            Appointment cancelled
          </h1>
          <p className="text-neutral-700">
            {state.booking.title} with {state.booking.host_name} has been cancelled.
          </p>
        </div>
      </main>
    )
  }

  const { booking, confirming, cancelling, error } = state

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-12">
      <div className="card w-full max-w-md text-center">
        <h1 className="mb-2 text-2xl font-display font-bold text-neutral-900">
          {booking.title} with {booking.host_name}
        </h1>
        <p className="mb-1 font-medium text-neutral-900">
          {longDateLabel(booking.start_at.slice(0, 10))}
        </p>
        <p className="mb-6 text-neutral-700">
          {slotTimeLabel(booking.start_at)} – {slotTimeLabel(booking.end_at)} (
          {booking.duration_minutes} min, {booking.host_name}&apos;s local time)
        </p>
        {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
        {!confirming ? (
          <Button
            variant="outline"
            className="w-full"
            onClick={() => setState({ kind: "ready", booking, confirming: true, cancelling: false, error: null })}
          >
            Cancel appointment
          </Button>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-neutral-700">Cancel this appointment?</p>
            <Button onClick={cancelBooking} disabled={cancelling} className="w-full">
              {cancelling ? "Cancelling…" : "Yes, cancel it"}
            </Button>
            <Button
              variant="outline"
              className="w-full"
              disabled={cancelling}
              onClick={() =>
                setState({ kind: "ready", booking, confirming: false, cancelling: false, error: null })
              }
            >
              Never mind
            </Button>
          </div>
        )}
      </div>
    </main>
  )
}

export default function ManageBookingPage({
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
          <p className="text-muted-foreground">Loading your booking…</p>
        </main>
      }
    >
      <ManageBookingInner slug={slug} />
    </Suspense>
  )
}
