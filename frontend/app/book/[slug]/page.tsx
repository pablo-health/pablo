// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { buildApiUrl } from "@/lib/api/client"
import { BookingConfirmedCard, type Confirmation } from "@/components/booking/BookingConfirmedCard"
import { longDateLabel, slotTimeLabel } from "@/lib/booking/time"

const TURNSTILE_SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js"
const CAPTCHA_FAILED_MESSAGE = "Verification failed. Please refresh and try again."

declare global {
  interface Window {
    turnstile?: {
      render: (
        container: HTMLElement,
        options: { sitekey: string; callback: (token: string) => void },
      ) => string
      reset: (widgetId: string) => void
    }
  }
}

/**
 * Public booking page (docs/design/public-booking.md).
 *
 * Unauthenticated by design: anyone with the link books directly against
 * the owner's availability. The API surface behind it is deliberately
 * narrow — display card, free slots for one date, one booking POST — and
 * everything here treats times as the practice's local wall-clock (the
 * `Z` suffix in slot strings is cosmetic, matching the engine).
 */

interface BookingLinkCard {
  slug: string
  host_name: string
  title: string
  description: string | null
  duration_minutes: number
  captcha_site_key: string | null
}

interface Slot {
  start: string
  end: string
}

interface SlotsResponse {
  date: string
  slots: Slot[]
  configured: boolean
}

/** Days shown in the picker. Must stay within the API's 60-day window. */
const DAYS_SHOWN = 14

function upcomingDates(): string[] {
  const dates: string[] = []
  const start = new Date()
  start.setDate(start.getDate() + 1) // start tomorrow: same-day slots may already be in the past
  for (let i = 0; i < DAYS_SHOWN; i++) {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, "0")
    const dd = String(d.getDate()).padStart(2, "0")
    dates.push(`${yyyy}-${mm}-${dd}`)
  }
  return dates
}

function dateLabel(dateStr: string): { weekday: string; day: string; month: string } {
  const [y, m, d] = dateStr.split("-").map(Number)
  const date = new Date(y, m - 1, d)
  return {
    weekday: date.toLocaleDateString("en-US", { weekday: "short" }),
    day: String(d),
    month: date.toLocaleDateString("en-US", { month: "short" }),
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(buildApiUrl(path))
  if (!resp.ok) {
    throw Object.assign(new Error(`Request failed: ${resp.status}`), { status: resp.status })
  }
  return resp.json()
}

export default function PublicBookingPage({
  params,
}: {
  params: Promise<{ slug: string }>
}) {
  const [slug, setSlug] = useState<string | null>(null)
  const dates = useMemo(upcomingDates, [])
  const [selectedDate, setSelectedDate] = useState(dates[0])
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null)
  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [email, setEmail] = useState("")
  const [note, setNote] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)

  useEffect(() => {
    let active = true
    void params.then(({ slug }) => {
      if (active) setSlug(slug)
    })
    return () => {
      active = false
    }
  }, [params])

  const linkQuery = useQuery<BookingLinkCard, Error & { status?: number }>({
    queryKey: ["public-booking-link", slug],
    queryFn: () => fetchJson(`/api/public/booking-links/${slug}`),
    enabled: slug !== null,
    retry: false,
  })

  const slotsQuery = useQuery<SlotsResponse>({
    queryKey: ["public-booking-slots", slug, selectedDate],
    queryFn: () => fetchJson(`/api/public/booking-links/${slug}/slots?date=${selectedDate}`),
    enabled: slug !== null && linkQuery.isSuccess && confirmation === null,
    // No retries: the public surface is rate-limited per IP, and the default
    // three retries turn a single 429 into four requests against the window
    // that just refused us.
    retry: false,
  })

  const captchaSiteKey = linkQuery.data?.captcha_site_key ?? null
  const [captchaToken, setCaptchaToken] = useState<string | null>(null)
  const captchaContainerRef = useRef<HTMLDivElement | null>(null)
  const captchaWidgetIdRef = useRef<string | null>(null)
  // The widget's container only exists once the booking form renders
  // (selectedSlot truthy), so re-run the mount effect when that flips.
  const captchaContainerMayExist = selectedSlot !== null

  useEffect(() => {
    if (!captchaSiteKey) return

    function mount() {
      if (!captchaContainerRef.current || !window.turnstile) return
      captchaWidgetIdRef.current = window.turnstile.render(captchaContainerRef.current, {
        sitekey: captchaSiteKey!,
        callback: setCaptchaToken,
      })
    }

    if (window.turnstile) {
      mount()
      return
    }
    const script = document.createElement("script")
    script.src = TURNSTILE_SCRIPT_SRC
    script.async = true
    script.onload = mount
    document.body.appendChild(script)
    return () => {
      document.body.removeChild(script)
    }
  }, [captchaSiteKey, captchaContainerMayExist])

  async function submitBooking(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedSlot || !slug) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const resp = await fetch(buildApiUrl(`/api/public/booking-links/${slug}/bookings`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(captchaToken ? { "X-Captcha-Token": captchaToken } : {}),
        },
        body: JSON.stringify({
          start_at: selectedSlot.start,
          first_name: firstName,
          last_name: lastName,
          email,
          note: note || null,
        }),
      })
      if (resp.status === 409) {
        setSubmitError("That time was just taken. Please pick another slot.")
        setSelectedSlot(null)
        void slotsQuery.refetch()
        return
      }
      if (resp.status === 403) {
        const body = await resp.json().catch(() => null)
        if (body?.error?.message === CAPTCHA_FAILED_MESSAGE) {
          setSubmitError(CAPTCHA_FAILED_MESSAGE)
          setCaptchaToken(null)
          if (captchaWidgetIdRef.current) {
            window.turnstile?.reset(captchaWidgetIdRef.current)
          }
          return
        }
        setSubmitError(
          "This practice isn't accepting online bookings right now. " +
            "Please contact them directly.",
        )
        return
      }
      if (resp.status === 429) {
        setSubmitError("Too many requests. Please wait a moment and try again.")
        return
      }
      if (!resp.ok) {
        setSubmitError("Something went wrong. Please try again.")
        return
      }
      setConfirmation(await resp.json())
    } catch {
      setSubmitError("Network error. Please check your connection and try again.")
    } finally {
      setSubmitting(false)
    }
  }

  if (slug === null || linkQuery.isLoading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-muted-foreground">Loading…</p>
      </main>
    )
  }

  if (linkQuery.isError) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center">
        <h1 className="text-2xl font-display font-bold text-neutral-900">
          This booking link isn&apos;t available
        </h1>
        <p className="max-w-md text-muted-foreground">
          It may have been turned off or the address may be mistyped. Please check with the
          person who sent it to you.
        </p>
      </main>
    )
  }

  const link = linkQuery.data!

  if (confirmation) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-12">
        {confirmation.status === "pending_confirmation" ? (
          <div className="card w-full max-w-md text-center">
            <Image
              src="/pablo-tie.webp"
              alt="Pablo bear"
              width={64}
              height={64}
              className="mx-auto mb-4"
            />
            <h1 className="mb-2 text-2xl font-display font-bold text-neutral-900">
              Almost there
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
            <p className="text-sm text-muted-foreground">
              Check your email to confirm — your hold expires in 15 minutes.
            </p>
          </div>
        ) : (
          <BookingConfirmedCard confirmation={confirmation} />
        )}
      </main>
    )
  }

  return (
    <main className="min-h-screen bg-background px-4 py-10">
      <div className="mx-auto w-full max-w-2xl">
        <div className="card mb-6">
          <p className="text-sm font-medium uppercase tracking-wide text-primary-700">
            {link.host_name}
          </p>
          <h1 className="mt-1 text-3xl font-display font-bold text-neutral-900">{link.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {link.duration_minutes} minutes · times shown in {link.host_name}&apos;s local time
          </p>
          {link.description && <p className="mt-3 text-neutral-700">{link.description}</p>}
        </div>

        <div className="card mb-6">
          {slotsQuery.isSuccess && !slotsQuery.data.configured ? (
            <p className="text-neutral-700">
              This host hasn&apos;t published their availability yet. Please contact them
              directly to schedule.
            </p>
          ) : (
            <>
              <h2 className="mb-3 text-lg font-display font-semibold text-neutral-900">
                Pick a day
              </h2>
              <div className="flex gap-2 overflow-x-auto pb-2">
                {dates.map((d) => {
                  const label = dateLabel(d)
                  const selected = d === selectedDate
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => {
                        setSelectedDate(d)
                        setSelectedSlot(null)
                      }}
                      className={`flex min-w-16 flex-col items-center rounded-lg border px-3 py-2 transition-colors ${
                        selected
                          ? "border-primary-500 bg-primary-50 text-primary-800"
                          : "border-border bg-card text-neutral-700 hover:border-primary-300"
                      }`}
                    >
                      <span className="text-xs">{label.weekday}</span>
                      <span className="text-lg font-semibold">{label.day}</span>
                      <span className="text-xs">{label.month}</span>
                    </button>
                  )
                })}
              </div>

              <h2 className="mb-3 mt-5 text-lg font-display font-semibold text-neutral-900">
                Pick a time
              </h2>
              {slotsQuery.isLoading && (
                <p className="text-muted-foreground">Checking availability…</p>
              )}
              {slotsQuery.isError && (
                <div className="flex flex-col items-start gap-2">
                  <p className="text-muted-foreground">
                    {(slotsQuery.error as (Error & { status?: number }) | null)?.status === 429
                      ? "Too many requests. Please wait a moment and try again."
                      : "Something went wrong loading availability. Please try again."}
                  </p>
                  <Button type="button" variant="outline" onClick={() => slotsQuery.refetch()}>
                    Try again
                  </Button>
                </div>
              )}
              {slotsQuery.isSuccess && slotsQuery.data.slots.length === 0 && (
                <p className="text-muted-foreground">
                  No openings on this day — try another date.
                </p>
              )}
              {slotsQuery.isSuccess && slotsQuery.data.slots.length > 0 && (
                <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                  {slotsQuery.data.slots.map((slot) => {
                    const selected = selectedSlot?.start === slot.start
                    return (
                      <button
                        key={slot.start}
                        type="button"
                        onClick={() => setSelectedSlot(slot)}
                        className={`rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                          selected
                            ? "border-primary-500 bg-primary-500 text-white"
                            : "border-border bg-card text-neutral-700 hover:border-primary-300"
                        }`}
                      >
                        {slotTimeLabel(slot.start)}
                      </button>
                    )
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {selectedSlot && (
          <form className="card" onSubmit={submitBooking}>
            <h2 className="mb-1 text-lg font-display font-semibold text-neutral-900">
              Your details
            </h2>
            <p className="mb-4 text-sm text-muted-foreground">
              {longDateLabel(selectedDate)} at {slotTimeLabel(selectedSlot.start)}
            </p>
            <div className="mb-4 grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="first-name">First name</Label>
                <Input
                  id="first-name"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  required
                  maxLength={100}
                />
              </div>
              <div>
                <Label htmlFor="last-name">Last name</Label>
                <Input
                  id="last-name"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  required
                  maxLength={100}
                />
              </div>
            </div>
            <div className="mb-4">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="mb-4">
              <Label htmlFor="note">Anything to share ahead of time? (optional)</Label>
              <Textarea
                id="note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={1000}
                rows={3}
              />
            </div>
            {captchaSiteKey && <div ref={captchaContainerRef} className="mb-4" />}
            {submitError && <p className="mb-3 text-sm text-red-600">{submitError}</p>}
            <Button
              type="submit"
              disabled={submitting || (captchaSiteKey !== null && !captchaToken)}
              className="w-full"
            >
              {submitting ? "Booking…" : "Confirm booking"}
            </Button>
          </form>
        )}
      </div>
    </main>
  )
}
