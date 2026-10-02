// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The cards the shell shows before there is a session: resolving, an address
 * that names no practice, and asking for and entering the code. Signing in by
 * email — the landing for anyone without a session — is `./EmailSignIn`.
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
  roomy = false,
}: {
  children: React.ReactNode
  testId: string
  /** More room inside, for the signed-out cards that stand on their own. */
  roomy?: boolean
}) {
  return (
    <div
      data-testid={testId}
      className={`rounded-lg border border-neutral-200 bg-white shadow-sm ${roomy ? "p-6 sm:p-8" : "p-6"}`}
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

/** Where a patient whose code did not work gets a new link. */
function RecoverLink({ slug, testId }: { slug: string; testId: string }) {
  return (
    <Link
      href={`/portal/${encodeURIComponent(slug)}/recover`}
      data-testid={testId}
      className="text-sm text-neutral-600 underline underline-offset-4"
    >
      Get a new sign-in link
    </Link>
  )
}

interface OtpCardProps {
  slug: string
  codeSent: boolean
  onRequestCode: () => void
  requestingCode: boolean
  otp: string
  onOtpChange: (value: string) => void
  onSubmit: () => void
  submitting: boolean
  error: string | null
  notice: string | null
  redeemFailed: boolean
}

export function OtpCard({
  slug,
  codeSent,
  onRequestCode,
  requestingCode,
  otp,
  onOtpChange,
  onSubmit,
  submitting,
  error,
  notice,
  redeemFailed,
}: OtpCardProps) {
  const errorLine = error && (
    <p data-testid="portal-shell-otp-error" className="mt-3 text-sm text-red-600">
      {error}
    </p>
  )

  if (!codeSent) {
    return (
      <CardShell testId="portal-shell-otp" roomy>
        <h2 className="text-xl font-semibold text-neutral-900">Get a sign-in code</h2>
        <p className="mt-1 text-sm text-neutral-600">
          We&apos;ll text a code to the mobile number your practice has for you.
        </p>
        {errorLine}
        <Button
          data-testid="portal-shell-request-code"
          onClick={onRequestCode}
          disabled={requestingCode}
          className="mt-4 w-full"
          size="lg"
        >
          {requestingCode ? "Sending…" : "Text me a code"}
        </Button>
      </CardShell>
    )
  }

  const canSubmit = otp.trim().length > 0 && !submitting
  return (
    <CardShell testId="portal-shell-otp" roomy>
      <h2 className="text-xl font-semibold text-neutral-900">Enter your code</h2>
      <p className="mt-1 text-sm text-neutral-600">
        We texted you a code. It works for 15 minutes.
      </p>
      {notice && (
        <p data-testid="portal-shell-code-notice" className="mt-2 text-sm text-neutral-600">
          {notice}
        </p>
      )}
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
      {errorLine}
      {redeemFailed && (
        <div className="mt-2">
          <RecoverLink slug={slug} testId="portal-shell-otp-recover" />
        </div>
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
      <Button
        data-testid="portal-shell-resend-code"
        onClick={onRequestCode}
        disabled={requestingCode}
        variant="link"
        className="mt-2 w-full"
      >
        {requestingCode ? "Sending…" : "Send a new code"}
      </Button>
    </CardShell>
  )
}
