// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The cards the shell shows before there is a session: resolving, an address
 * that names no practice, no session, and entering the code.
 *
 * Every redeem failure reaches `OtpCard` as the same message. See
 * `PortalShell` for why that sameness is the point.
 */

"use client"

import Link from "next/link"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function CardShell({
  children,
  testId,
}: {
  children: React.ReactNode
  testId: string
}) {
  return (
    <div
      data-testid={testId}
      className="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm"
    >
      {children}
    </div>
  )
}

export function ResolvingCard() {
  return (
    <CardShell testId="portal-shell-skeleton">
      <div className="flex flex-col items-center gap-3 py-8 text-neutral-500">
        <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
        <p className="text-sm">Loading…</p>
      </div>
    </CardShell>
  )
}

export function UnknownPracticeCard() {
  return (
    <CardShell testId="portal-shell-unknown">
      <div className="flex flex-col items-center gap-2 py-4 text-center">
        <h2 className="text-base font-semibold text-neutral-900">This link isn&apos;t right</h2>
        <p className="text-sm text-neutral-600">
          Double check the address, or ask your practice to send you a new link.
        </p>
      </div>
    </CardShell>
  )
}

export function NoSessionCard({ slug, revoked = false }: { slug: string; revoked?: boolean }) {
  return (
    <CardShell testId="portal-shell-no-session">
      <div className="flex flex-col items-center gap-2 py-4 text-center">
        <h2 className="text-base font-semibold text-neutral-900">Check your email</h2>
        <p className="text-sm text-neutral-600">
          Look for your invite link — or ask your practice to send one.
        </p>
        {revoked && (
          <p data-testid="portal-shell-expired-note" className="mt-2 text-sm text-neutral-500">
            Your access has ended. Ask your practice to send a new invite link when you&apos;re
            ready to continue.
          </p>
        )}
        {/* Offered on both, because the shell cannot tell a lapsed session
            from a withdrawn one and neither can the recovery page — it
            answers the same way either way. */}
        <Link
          href={`/portal/${encodeURIComponent(slug)}/recover`}
          data-testid="portal-shell-recover-link"
          className="mt-2 text-sm text-neutral-600 underline underline-offset-4"
        >
          Send me a new link
        </Link>
      </div>
    </CardShell>
  )
}

export function OtpCard({
  otp,
  onOtpChange,
  onSubmit,
  submitting,
  error,
}: {
  otp: string
  onOtpChange: (value: string) => void
  onSubmit: () => void
  submitting: boolean
  error: string | null
}) {
  const canSubmit = otp.trim().length > 0 && !submitting
  return (
    <CardShell testId="portal-shell-otp">
      <h2 className="text-base font-semibold text-neutral-900">Enter your code</h2>
      <p className="mt-1 text-sm text-neutral-600">
        We sent a code by text message. Enter it below to continue.
      </p>
      <div className="mt-4">
        <Label htmlFor="portal-otp">Code</Label>
        <Input
          id="portal-otp"
          data-testid="portal-shell-otp-input"
          value={otp}
          onChange={(e) => onOtpChange(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          className="mt-1"
        />
      </div>
      {error && (
        <p data-testid="portal-shell-otp-error" className="mt-3 text-sm text-red-600">
          {error}
        </p>
      )}
      <Button
        data-testid="portal-shell-otp-submit"
        onClick={onSubmit}
        disabled={!canSubmit}
        className="mt-4 w-full"
        size="lg"
      >
        {submitting ? "Checking…" : "Continue"}
      </Button>
    </CardShell>
  )
}
