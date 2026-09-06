// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One claim: where it stands, the hops it has passed, what the scrub finds,
 * each line's adjudication, and the actions its state allows.
 *
 * A draft is reviewed and filed from here. A claim that has left the
 * practice is never edited in place: "Correct and resubmit" builds a
 * replacement from today's sources and "Void" tells the payer to
 * disregard it, and both are new claims that name this one as parent.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Download } from "lucide-react"
import { useClaim, useCorrectClaim, useValidateClaim, useVoidClaim } from "@/hooks/useClaims"
import { blockingFindingsFrom, downloadClaimCms1500 } from "@/lib/api/claims"
import { formatCents } from "@/lib/money"
import type { ClaimDetailResponse, ClaimFinding } from "@/types/claims"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ClaimStateBadge, DeadlineBadge } from "./ClaimBadges"
import { ClaimFindings } from "./ClaimFindings"
import { ClaimHops } from "./ClaimHops"
import { ClaimLinesTable } from "./ClaimLinesTable"
import { canCorrectOrVoid, canReviewAndFile, frequencyLabel } from "./claimPresentation"

interface ClaimDetailProps {
  claimId: string
}

export function ClaimDetail({ claimId }: ClaimDetailProps) {
  const { data: claim, isLoading, error } = useClaim(claimId)

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }
  if (error || !claim) {
    return (
      <div className="card py-12 text-center">
        <p className="text-sm text-red-700">This claim could not be loaded.</p>
      </div>
    )
  }
  return <LoadedClaim claim={claim} />
}

function LoadedClaim({ claim }: { claim: ClaimDetailResponse }) {
  const router = useRouter()
  const validate = useValidateClaim()
  const correct = useCorrectClaim()
  const voidClaim = useVoidClaim()
  const [blocked, setBlocked] = useState<ClaimFinding[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [confirmVoid, setConfirmVoid] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const kind = frequencyLabel(claim.frequency_code)
  const findings = blocked ?? claim.findings
  const hasBlocking = findings.some((f) => f.severity === "blocking")
  const busy = validate.isPending || correct.isPending || voidClaim.isPending

  async function handleFile() {
    setFailure(null)
    setBlocked(null)
    try {
      await validate.mutateAsync({ claimId: claim.id })
    } catch (error) {
      const stops = blockingFindingsFrom(error)
      if (stops) setBlocked(stops)
      else setFailure("The claim could not be filed. Try again in a moment.")
    }
  }

  async function handleCorrect() {
    setFailure(null)
    try {
      const child = await correct.mutateAsync({ claimId: claim.id })
      router.push(`/dashboard/billing/claims/${child.id}`)
    } catch {
      setFailure("A corrected claim could not be built. Check the visit and the coverage on file.")
    }
  }

  async function handleVoid() {
    setFailure(null)
    try {
      const child = await voidClaim.mutateAsync({ claimId: claim.id })
      setConfirmVoid(false)
      router.push(`/dashboard/billing/claims/${child.id}`)
    } catch {
      setFailure("The void could not be filed. Try again in a moment.")
    }
  }

  async function handleDownload() {
    setDownloading(true)
    setFailure(null)
    try {
      const blob = await downloadClaimCms1500(claim.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `claim-${claim.control_number}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setFailure("The CMS-1500 could not be prepared.")
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="space-y-6" data-testid="claim-detail" data-state={claim.state}>
      <div className="card space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-mono text-xs text-neutral-500">Claim {claim.control_number}</p>
            <h1 className="text-2xl font-display font-semibold text-neutral-900">
              <Link href={`/dashboard/patients/${claim.patient_id}`} className="hover:underline">
                {claim.patient_name}
              </Link>
            </h1>
            <p className="text-sm text-neutral-600">
              {claim.payer_name ?? "Unknown payer"}
              {kind && <span className="ml-2 text-neutral-500">· {kind}</span>}
              {claim.parent_claim_id && (
                <>
                  {" "}
                  of{" "}
                  <Link
                    href={`/dashboard/billing/claims/${claim.parent_claim_id}`}
                    className="underline"
                  >
                    the original claim
                  </Link>
                </>
              )}
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <ClaimStateBadge state={claim.state} />
            <DeadlineBadge deadlines={claim.deadlines} state={claim.state} />
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
          <dt className="text-neutral-500">Charged</dt>
          <dd className="text-neutral-900">{formatCents(claim.total_charge_cents)}</dd>
          <dt className="text-neutral-500">Paid</dt>
          <dd className="text-neutral-900">
            {claim.adjudicated_at ? formatCents(claim.total_paid_cents) : "Pending adjudication"}
          </dd>
        </dl>

        {failure && (
          <p role="alert" className="text-sm text-red-700">
            {failure}
          </p>
        )}

        <div className="flex flex-wrap gap-2" data-testid="claim-actions">
          {canReviewAndFile(claim.state) && (
            <Button
              data-testid="review-and-file"
              onClick={handleFile}
              disabled={hasBlocking || busy}
            >
              {validate.isPending ? "Filing…" : "Review and file"}
            </Button>
          )}
          {canCorrectOrVoid(claim.state, claim.frequency_code) && (
            <>
              <Button data-testid="correct-claim" onClick={handleCorrect} disabled={busy}>
                {correct.isPending ? "Building…" : "Correct and resubmit"}
              </Button>
              {confirmVoid ? (
                <>
                  <Button
                    variant="destructive"
                    data-testid="confirm-void"
                    onClick={handleVoid}
                    disabled={busy}
                  >
                    {voidClaim.isPending ? "Voiding…" : "Yes, void this claim"}
                  </Button>
                  <Button variant="outline" onClick={() => setConfirmVoid(false)} disabled={busy}>
                    Keep it
                  </Button>
                </>
              ) : (
                <Button
                  variant="outline"
                  data-testid="void-claim"
                  onClick={() => setConfirmVoid(true)}
                  disabled={busy}
                >
                  Void
                </Button>
              )}
            </>
          )}
          {claim.state !== "draft" && (
            <Button variant="outline" onClick={handleDownload} disabled={downloading}>
              <Download aria-hidden />
              {downloading ? "Preparing…" : "CMS-1500 PDF"}
            </Button>
          )}
        </div>
      </div>

      <div className="card space-y-3">
        <h2 className="text-lg font-display font-semibold text-neutral-900">Where it is</h2>
        <ClaimHops hops={claim.hops} />
      </div>

      <div className="card space-y-3">
        <h2 className="text-lg font-display font-semibold text-neutral-900">Checks</h2>
        <ClaimFindings findings={findings} emptyText="The claim passes every check." />
      </div>

      <div className="card space-y-3">
        <h2 className="text-lg font-display font-semibold text-neutral-900">Lines</h2>
        <ClaimLinesTable lines={claim.lines} adjudicated={claim.adjudicated_at !== null} />
      </div>
    </div>
  )
}
