// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { Button } from "@/components/ui/button"
import { longDateLabel, slotTimeLabel } from "@/lib/booking/time"

export interface Confirmation {
  host_name: string
  title: string
  start_at: string
  end_at: string
  duration_minutes: number
  status: "confirmed" | "pending_confirmation"
}

/** Floating local time ICS (no TZID): matches the engine's wall-clock model. */
function buildIcs(confirmation: Confirmation): string {
  const compact = (s: string) => s.slice(0, 19).replace(/[-:]/g, "")
  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Pablo//Booking//EN",
    "BEGIN:VEVENT",
    `UID:${crypto.randomUUID()}@pablo`,
    `DTSTAMP:${compact(new Date().toISOString())}Z`,
    `DTSTART:${compact(confirmation.start_at)}`,
    `DTEND:${compact(confirmation.end_at)}`,
    `SUMMARY:${confirmation.title} with ${confirmation.host_name}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n")
}

function downloadIcs(confirmation: Confirmation) {
  const blob = new Blob([buildIcs(confirmation)], { type: "text/calendar" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = "appointment.ics"
  anchor.click()
  URL.revokeObjectURL(url)
}

/** Confirmed-booking card shared by the booking page and the confirm-link page. */
export function BookingConfirmedCard({ confirmation }: { confirmation: Confirmation }) {
  return (
    <div className="card w-full max-w-md text-center">
      <Image
        src="/pablo-tie.webp"
        alt="Pablo bear"
        width={64}
        height={64}
        className="mx-auto mb-4"
      />
      <h1 className="mb-2 text-2xl font-display font-bold text-neutral-900">
        You&apos;re booked
      </h1>
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
      <Button onClick={() => downloadIcs(confirmation)} className="w-full">
        Add to calendar (.ics)
      </Button>
    </div>
  )
}
