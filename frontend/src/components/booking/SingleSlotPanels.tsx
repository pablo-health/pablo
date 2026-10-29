// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import type { Confirmation } from "@/components/booking/BookingConfirmedCard"
import { longDateLabel, slotTimeLabel } from "@/lib/booking/time"

/**
 * The two endings a single-slot booking link has that an ordinary link does
 * not (docs/design/public-booking.md): the time is gone, or the practice
 * reviews self-bookings and the time is now held as a request.
 */

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-12">
      <div className="card w-full max-w-md text-center">
        <Image src="/pablo-tie.webp" alt="Pablo bear" width={64} height={64} className="mx-auto mb-4" />
        {children}
      </div>
    </main>
  )
}

/** Shown to anyone who arrives after the link's one time was booked or passed. */
export function SlotTakenPanel({ hostName }: { hostName: string }) {
  return (
    <Panel>
      <h1 className="mb-2 text-2xl font-display font-bold text-neutral-900">
        This time has been taken
      </h1>
      <p className="text-neutral-700">
        Please contact {hostName} to find another time.
      </p>
    </Panel>
  )
}

/**
 * The practice confirms self-bookings itself, so the time is held as a
 * request. Nothing is sent from here, so the copy promises nothing about
 * being contacted — only what is true now: the request exists and the time
 * is held.
 */
export function RequestSentPanel({ confirmation }: { confirmation: Confirmation }) {
  return (
    <Panel>
      <h1 className="mb-2 text-2xl font-display font-bold text-neutral-900">Request sent</h1>
      <p className="mb-1 text-neutral-700">
        {confirmation.title} with {confirmation.host_name}
      </p>
      <p className="mb-1 font-medium text-neutral-900">
        {longDateLabel(confirmation.start_at.slice(0, 10))}
      </p>
      <p className="mb-6 text-neutral-700">
        {slotTimeLabel(confirmation.start_at)} – {slotTimeLabel(confirmation.end_at)} (
        {confirmation.duration_minutes} min, {confirmation.host_name}&apos;s local time)
      </p>
      <p className="text-sm text-muted-foreground">
        The time is held for you while {confirmation.host_name} reviews your request.
      </p>
    </Panel>
  )
}
