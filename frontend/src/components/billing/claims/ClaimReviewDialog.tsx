// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The review step between "File claim" and a queued claim.
 *
 * Opening the dialog builds a draft from the visit (or picks up the draft
 * already on it), shows what the scrub finds, and offers "Review and file".
 * Filing runs the scrub for real: a clean claim becomes `validated` and
 * reads "Queued to send"; a blocking finding comes back as a refusal, is
 * listed here, and the button stays disabled until the visit or the
 * coverage is fixed and the claim rebuilt. Nothing here sends anything —
 * a validated claim is picked up by the outbox.
 *
 * The body is mounted only while the dialog is open, so every open starts
 * from a clean state without an effect having to reset anything.
 */

"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useBuildClaim, useClaim, useValidateClaim } from "@/hooks/useClaims"
import { blockingFindingsFrom } from "@/lib/api/claims"
import { formatCents } from "@/lib/money"
import type { ClaimFinding } from "@/types/claims"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { ClaimStateBadge } from "./ClaimBadges"
import { ClaimFindings } from "./ClaimFindings"
import { formatIsoDate } from "./claimPresentation"

interface ClaimReviewDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The visit to build a claim from; ignored when `claimId` is given. */
  appointmentId: string
  patientName: string
  /** A draft already on the visit, to review instead of building another. */
  claimId?: string
}

export function ClaimReviewDialog({ open, onOpenChange, ...body }: ClaimReviewDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>File a claim for {body.patientName}</DialogTitle>
          <DialogDescription>
            The claim is built from the visit, the coverage on file and the practice&rsquo;s
            billing identity. Filing checks it and queues it to send.
          </DialogDescription>
        </DialogHeader>
        {open && <ReviewBody {...body} onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}

interface ReviewBodyProps {
  appointmentId: string
  claimId?: string
  onClose: () => void
}

function ReviewBody({ appointmentId, claimId: existingClaimId, onClose }: ReviewBodyProps) {
  const build = useBuildClaim()
  const validate = useValidateClaim()
  const [claimId, setClaimId] = useState<string | undefined>(existingClaimId)
  const [failure, setFailure] = useState<string | null>(null)
  const [blocked, setBlocked] = useState<ClaimFinding[] | null>(null)
  const [filed, setFiled] = useState(false)
  const claim = useClaim(claimId)

  const { mutateAsync: buildClaim } = build

  useEffect(() => {
    if (existingClaimId) return
    let cancelled = false
    buildClaim({ appointmentId })
      .then((built) => {
        if (!cancelled) setClaimId(built.id)
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setFailure(
          error instanceof Error && error.message
            ? error.message
            : "The claim could not be built from this session.",
        )
      })
    return () => {
      cancelled = true
    }
  }, [existingClaimId, appointmentId, buildClaim])

  async function handleFile() {
    if (!claimId) return
    setBlocked(null)
    try {
      await validate.mutateAsync({ claimId })
      setFiled(true)
    } catch (error) {
      const findings = blockingFindingsFrom(error)
      if (findings) {
        setBlocked(findings)
      } else {
        setFailure("The claim could not be filed. Try again in a moment.")
      }
    }
  }

  const detail = claim.data
  const findings = blocked ?? detail?.findings ?? []
  const hasBlocking = findings.some((f) => f.severity === "blocking")
  const isDraft = detail?.state === "draft"
  const loading = build.isPending || (claimId !== undefined && claim.isLoading)

  return (
    <>
      {failure && (
        <p role="alert" className="text-sm text-red-700">
          {failure}
        </p>
      )}

      {loading && !failure && (
        <div className="space-y-2">
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}

      {detail && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="font-mono text-xs text-neutral-600">
              Claim {detail.control_number}
            </span>
            <ClaimStateBadge state={filed ? "validated" : detail.state} />
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-neutral-500">Payer</dt>
            <dd className="text-neutral-900">{detail.payer_name ?? "Unknown payer"}</dd>
            <dt className="text-neutral-500">Total charge</dt>
            <dd className="text-neutral-900">{formatCents(detail.total_charge_cents)}</dd>
          </dl>

          <ul className="divide-y divide-neutral-100 rounded-md border border-neutral-200 text-sm">
            {detail.lines.map((line) => (
              <li key={line.id} className="flex items-center justify-between px-3 py-2">
                <span className="text-neutral-900">
                  {line.cpt}
                  {line.modifiers.length > 0 && (
                    <span className="text-neutral-500"> {line.modifiers.join(" ")}</span>
                  )}
                  <span className="ml-2 text-xs text-neutral-500">
                    {formatIsoDate(line.service_date)}
                  </span>
                </span>
                <span className="text-neutral-900">{formatCents(line.charge_cents)}</span>
              </li>
            ))}
          </ul>

          {filed ? (
            <p className="text-sm text-emerald-800">
              Queued to send. Follow it on the{" "}
              <Link href={`/dashboard/billing/claims/${detail.id}`} className="underline">
                claim page
              </Link>
              .
            </p>
          ) : (
            <ClaimFindings
              findings={findings}
              emptyText="Nothing stops this claim from being filed."
            />
          )}
        </div>
      )}

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          {filed ? "Done" : "Not now"}
        </Button>
        {!filed && (
          <Button
            data-testid="review-and-file"
            onClick={handleFile}
            disabled={!detail || !isDraft || hasBlocking || validate.isPending}
          >
            {validate.isPending ? "Filing…" : "Review and file"}
          </Button>
        )}
      </DialogFooter>
    </>
  )
}
