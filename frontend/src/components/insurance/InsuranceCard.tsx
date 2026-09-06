// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * InsuranceCard
 *
 * The chart's coverage on file: the payer, the ids off the card, who the
 * subscriber is, and whether an eligibility check has confirmed any of it.
 * Adding or editing goes through `CoverageDialog`; removing deactivates the
 * row server-side rather than deleting it, so a claim filed under the old
 * plan still has something to point at.
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
import { useDeactivateCoverage, usePatientCoverage } from "@/hooks/useCoverage"
import type { CoverageResponse, SubscriberRelationship } from "@/types/coverage"
import { CoverageDialog } from "./CoverageDialog"

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

function verificationLabel(coverage: CoverageResponse): string {
  if (!coverage.verified_at) return "Not yet verified"
  return `Verified ${new Date(coverage.verified_at).toLocaleDateString()}`
}

interface InsuranceCardProps {
  patientId: string
}

export function InsuranceCard({ patientId }: InsuranceCardProps) {
  const { data: coverage, isLoading, error } = usePatientCoverage(patientId)
  const deactivate = useDeactivateCoverage()
  const { readOnly } = useReadOnlyMode()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)

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

  return (
    <div className="space-y-3">
      {coverage ? (
        <div className="rounded-lg border border-neutral-100 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-neutral-900">{coverage.payer.name}</p>
              <p className="text-xs text-neutral-500">
                Payer ID {coverage.payer.payer_id} · {verificationLabel(coverage)}
              </p>
            </div>
            {!readOnly && (
              <div className="flex shrink-0 gap-2">
                <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
                  Edit
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmRemove(true)}>
                  Remove
                </Button>
              </div>
            )}
          </div>
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
