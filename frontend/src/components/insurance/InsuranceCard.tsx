// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * InsuranceCard
 *
 * The chart's coverage on file: the payer, the ids off the card, who the
 * subscriber is, and what the last eligibility check found. Adding or
 * editing goes through `CoverageDialog`; removing deactivates the row
 * server-side rather than deleting it, so a claim filed under the old plan
 * still has something to point at. "Re-verify" asks the payer again, now.
 *
 * The eligibility answer is rendered as what the payer knew when asked —
 * never as a payment guarantee (see `EligibilityBadge`).
 */

"use client"

import { useState } from "react"
import { ShieldCheck } from "lucide-react"
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
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { ApiError } from "@/lib/api/client"
import { formatCents } from "@/lib/money"
import {
  useDeactivateCoverage,
  usePatientCoverage,
  useVerifyCoverage,
} from "@/hooks/useCoverage"
import type {
  CoverageResponse,
  EligibilitySummary,
  SubscriberRelationship,
} from "@/types/coverage"
import { CoverageDialog } from "./CoverageDialog"
import { EligibilityBadge, carveoutText } from "./EligibilityBadge"

const RELATIONSHIP_LABEL: Record<SubscriberRelationship, string> = {
  self: "Self",
  spouse: "Spouse",
  child: "Child",
  other: "Other",
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="text-sm text-neutral-900">{value}</dd>
    </div>
  )
}

function subscriberSummary(coverage: CoverageResponse): string {
  const relationship = RELATIONSHIP_LABEL[coverage.subscriber_relationship]
  if (coverage.subscriber_relationship === "self") return relationship
  const name = [coverage.subscriber_first_name, coverage.subscriber_last_name]
    .filter(Boolean)
    .join(" ")
  return name ? `${relationship} — ${name}` : relationship
}

function visitLimitText(summary: EligibilitySummary): string | null {
  const limit = summary.visit_limit
  if (!limit) return null
  if (limit.remaining != null && limit.total != null) {
    return `${limit.remaining} of ${limit.total} remaining`
  }
  if (limit.remaining != null) return `${limit.remaining} remaining`
  return `${limit.total} per plan year`
}

function percentText(pct: number | null): string | null {
  if (pct == null) return null
  return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`
}

/** What the payer said about the behavioral benefit, as the card lists it. */
function EligibilityDetails({ summary }: { summary: EligibilitySummary }) {
  if (summary.status === "error") {
    return (
      <div className="mt-3 space-y-2" data-testid="eligibility-details">
        {summary.aaa_errors.map((error) => (
          <div key={error.code} className="rounded-md bg-yellow-50 px-3 py-2 text-sm">
            <p className="font-medium text-yellow-800">
              {error.description} ({error.code}) — {error.followup_action}
            </p>
            {error.resolution && (
              <p className="mt-1 whitespace-pre-line text-xs text-yellow-800">
                {error.resolution}
              </p>
            )}
          </div>
        ))}
      </div>
    )
  }

  const carveout = carveoutText(summary)
  const authorization =
    summary.requires_authorization == null
      ? null
      : summary.requires_authorization
        ? "Required"
        : "Not required"

  return (
    <div className="mt-3 space-y-3" data-testid="eligibility-details">
      {carveout && (
        <p className="rounded-md bg-yellow-50 px-3 py-2 text-sm font-medium text-yellow-800">
          {carveout}
        </p>
      )}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
        <Field label="Plan" value={summary.plan_name} />
        <Field
          label="Copay"
          value={summary.copay_cents == null ? null : formatCents(summary.copay_cents)}
        />
        <Field label="Coinsurance" value={percentText(summary.coinsurance_pct)} />
        <Field
          label="Deductible remaining"
          value={
            summary.deductible_remaining_cents == null
              ? null
              : formatCents(summary.deductible_remaining_cents)
          }
        />
        <Field label="Visit limit" value={visitLimitText(summary)} />
        <Field label="Authorization" value={authorization} />
      </dl>
      <p className="text-xs text-neutral-500">
        What the payer reported for outpatient mental health when asked. Not a payment
        guarantee.
      </p>
    </div>
  )
}

interface InsuranceCardProps {
  patientId: string
}

export function InsuranceCard({ patientId }: InsuranceCardProps) {
  const { data: coverage, isLoading, error } = usePatientCoverage(patientId)
  const deactivate = useDeactivateCoverage()
  const verify = useVerifyCoverage()
  const { readOnly } = useReadOnlyMode()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)

  if (isLoading) return <Skeleton className="h-24 w-full" />

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load insurance."}
      </p>
    )
  }

  function handleRemove() {
    deactivate.mutate({ patientId }, { onSuccess: () => setConfirmRemove(false) })
  }

  function handleVerify() {
    setVerifyError(null)
    verify.mutate(
      { patientId },
      {
        onError: (err) => {
          setVerifyError(
            err instanceof ApiError || err instanceof Error
              ? err.message
              : "The check could not be run.",
          )
        },
      },
    )
  }

  return (
    <div className="space-y-3">
      {coverage ? (
        <div className="rounded-lg border border-neutral-100 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1.5">
              <p className="text-sm font-medium text-neutral-900">{coverage.payer.name}</p>
              <p className="text-xs text-neutral-500">Payer ID {coverage.payer.payer_id}</p>
              <EligibilityBadge summary={coverage.eligibility} />
            </div>
            {!readOnly && (
              <div className="flex shrink-0 gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleVerify}
                  disabled={verify.isPending}
                >
                  {verify.isPending ? "Checking…" : "Re-verify"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
                  Edit
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmRemove(true)}>
                  Remove
                </Button>
              </div>
            )}
          </div>
          {verifyError && (
            <p className="mt-2 text-sm text-red-500" role="alert">
              {verifyError}
            </p>
          )}
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
            <Field label="Member ID" value={coverage.member_id} />
            <Field label="Group number" value={coverage.group_number} />
            <Field label="Plan" value={coverage.plan_name} />
            <Field label="Subscriber" value={subscriberSummary(coverage)} />
            <Field
              label="Subscriber date of birth"
              value={
                coverage.subscriber_relationship === "self"
                  ? null
                  : coverage.subscriber_date_of_birth
              }
            />
          </dl>
          {coverage.eligibility && <EligibilityDetails summary={coverage.eligibility} />}
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <ShieldCheck className="h-8 w-8 text-neutral-300" />
          <p className="text-sm text-neutral-600">No insurance on file for this client.</p>
          {!readOnly && <Button onClick={() => setDialogOpen(true)}>Add coverage</Button>}
        </div>
      )}

      <CoverageDialog
        patientId={patientId}
        coverage={coverage ?? null}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />

      <Dialog open={confirmRemove} onOpenChange={setConfirmRemove}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove this coverage?</DialogTitle>
            <DialogDescription>
              The plan comes off the chart. Anything already filed under it keeps its record.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmRemove(false)}
              disabled={deactivate.isPending}
            >
              Keep it
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleRemove}
              disabled={deactivate.isPending}
            >
              Remove coverage
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
