// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PayersCard
 *
 * The practice's insurance payers and, per payer, the three deadlines a claim
 * against it lives under. The defaults are the common floor; a practice's
 * participation agreement can say otherwise, and this is where it says so.
 *
 * Each payer also shows where the practice stands with it for electronic
 * transactions: the enrollment requests filed through the clearinghouse and
 * what the payer is waiting on, with an "Enroll with payer" button for a
 * payer that has nothing on file yet.
 */

"use client"

import { ChevronDown, ChevronUp, Plus } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SettingsCard } from "@/components/settings/ui"
import {
  useCreatePayer,
  usePayerEnrollments,
  usePayers,
  useRequestPayerEnrollments,
  useUpdatePayer,
} from "@/hooks/useCoverage"
import type {
  EnrollmentRequestStatus,
  EnrollmentStatus,
  EnrollmentTransactionType,
  PayerEnrollmentResponse,
  PayerResponse,
  UpdatePayerRequest,
} from "@/types/coverage"

export const DEADLINE_HELP =
  "From your participation agreement; the defaults are the common floor."

export const ENROLLMENT_HELP =
  "Filed through your clearinghouse account. Remittance always needs one; claims and eligibility only when the payer says so."

type DeadlineField = "timely_filing_days" | "corrected_claim_days" | "appeal_days"

const DEADLINES: { field: DeadlineField; label: string }[] = [
  { field: "timely_filing_days", label: "Timely filing (days)" },
  { field: "corrected_claim_days", label: "Corrected claim (days)" },
  { field: "appeal_days", label: "Appeal (days)" },
]

export const ENROLLMENT_STATUS_LABELS: Record<EnrollmentStatus, string> = {
  none: "Not enrolled",
  filed: "Enrollment filed",
  pending: "Enrollment in progress",
  active: "Enrolled",
  error: "Enrollment rejected",
}

const TRANSACTION_LABELS: Record<EnrollmentTransactionType, string> = {
  "837P": "Claims",
  "270": "Eligibility",
  "835": "Remittance",
}

const REQUEST_STATUS_LABELS: Record<EnrollmentRequestStatus, string> = {
  draft: "Draft",
  stedi_action_required: "Submitted",
  provider_action_required: "Needs your action",
  provisioning: "With the payer",
  live: "Live",
  rejected: "Rejected",
  canceled: "Canceled",
}

function EnrollmentRequestRow({ request }: { request: PayerEnrollmentResponse }) {
  const needsAction = request.status === "provider_action_required"
  return (
    <li className="py-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-foreground">{TRANSACTION_LABELS[request.transaction_type]}</span>
        <span className={needsAction ? "font-semibold text-foreground" : "text-muted-foreground"}>
          {REQUEST_STATUS_LABELS[request.status]}
        </span>
      </div>
      {request.instructions && (
        <p className="mt-1 whitespace-pre-line text-[12.5px] text-muted-foreground">
          {request.instructions}
        </p>
      )}
    </li>
  )
}

function PayerEnrollments({ payer }: { payer: PayerResponse }) {
  const { data } = usePayerEnrollments(payer.id)
  const request = useRequestPayerEnrollments()
  const requests = data?.data ?? []
  const error = request.error instanceof Error ? request.error.message : null

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-foreground">
          {ENROLLMENT_STATUS_LABELS[data?.enrollment_status ?? payer.enrollment_status]}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => request.mutate({ payerRowId: payer.id })}
          disabled={request.isPending}
        >
          Enroll with payer
        </Button>
      </div>
      {requests.length > 0 && (
        <ul className="m-0 list-none divide-y divide-border p-0">
          {requests.map((r) => (
            <EnrollmentRequestRow key={r.transaction_type} request={r} />
          ))}
        </ul>
      )}
      {error && <p className="text-[12.5px] text-destructive">{error}</p>}
      <p className="text-[12.5px] text-muted-foreground">{ENROLLMENT_HELP}</p>
    </div>
  )
}

function PayerRow({
  payer,
  open,
  onToggle,
  onChange,
}: {
  payer: PayerResponse
  open: boolean
  onToggle: () => void
  onChange: (patch: UpdatePayerRequest) => void
}) {
  function commitDays(field: DeadlineField, raw: string) {
    const days = Number(raw)
    if (Number.isInteger(days) && days > 0 && days !== payer[field]) onChange({ [field]: days })
  }

  return (
    <li className="border-t border-border first:border-t-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 border-0 bg-transparent px-0 py-3 text-left"
      >
        <span>
          <span className="block text-sm font-semibold text-foreground">{payer.name}</span>
          <span className="block text-[12.5px] text-muted-foreground">
            Payer ID {payer.payer_id} · files within {payer.timely_filing_days} days ·{" "}
            {ENROLLMENT_STATUS_LABELS[payer.enrollment_status]}
          </span>
        </span>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      {open && (
        <div className="space-y-3 pb-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`payer-name-${payer.id}`}>Name</Label>
              <Input
                id={`payer-name-${payer.id}`}
                defaultValue={payer.name}
                onBlur={(e) => {
                  const name = e.target.value.trim()
                  if (name && name !== payer.name) onChange({ name })
                }}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`payer-id-${payer.id}`}>Payer ID</Label>
              <Input
                id={`payer-id-${payer.id}`}
                defaultValue={payer.payer_id}
                onBlur={(e) => {
                  const payerId = e.target.value.trim()
                  if (payerId && payerId !== payer.payer_id) onChange({ payer_id: payerId })
                }}
              />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            {DEADLINES.map(({ field, label }) => (
              <div key={field} className="grid gap-1.5">
                <Label htmlFor={`${field}-${payer.id}`}>{label}</Label>
                <Input
                  id={`${field}-${payer.id}`}
                  type="number"
                  min={1}
                  defaultValue={payer[field]}
                  onBlur={(e) => commitDays(field, e.target.value)}
                />
              </div>
            ))}
          </div>
          <p className="text-[12.5px] text-muted-foreground">{DEADLINE_HELP}</p>
          <PayerEnrollments payer={payer} />
        </div>
      )}
    </li>
  )
}

export function PayersCard() {
  const { data } = usePayers()
  const createPayer = useCreatePayer()
  const updatePayer = useUpdatePayer()
  const [openId, setOpenId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState("")
  const [newPayerId, setNewPayerId] = useState("")

  const payers = data?.data ?? []

  function handleAdd() {
    const name = newName.trim()
    const payerId = newPayerId.trim()
    if (!name || !payerId) return
    createPayer.mutate(
      { name, payer_id: payerId },
      {
        onSuccess: (created) => {
          setNewName("")
          setNewPayerId("")
          setAdding(false)
          setOpenId(created.id)
        },
      }
    )
  }

  return (
    <SettingsCard
      title="Insurance payers"
      description="Who you file claims with, and the deadlines each one holds you to."
      flush
    >
      <div className="px-[22px] pt-1.5 pb-5">
        {payers.length === 0 && !adding && (
          <p className="py-3 text-sm text-muted-foreground">
            No payers yet. One is added the first time a client&apos;s coverage names it, or add one here.
          </p>
        )}
        <ul className="m-0 list-none p-0">
          {payers.map((payer) => (
            <PayerRow
              key={payer.id}
              payer={payer}
              open={openId === payer.id}
              onToggle={() => setOpenId(openId === payer.id ? null : payer.id)}
              onChange={(patch) => updatePayer.mutate({ id: payer.id, data: patch })}
            />
          ))}
        </ul>
        {adding ? (
          <div className="mt-2 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="grid gap-1.5">
              <Label htmlFor="new-payer-name">Name</Label>
              <Input
                id="new-payer-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Aetna"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="new-payer-id">Payer ID</Label>
              <Input
                id="new-payer-id"
                value={newPayerId}
                onChange={(e) => setNewPayerId(e.target.value)}
                placeholder="e.g. 60054"
              />
            </div>
            <div className="flex gap-2">
              <Button type="button" size="sm" onClick={handleAdd} disabled={createPayer.isPending}>
                Add
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setAdding(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="mt-2">
            <Button type="button" size="sm" onClick={() => setAdding(true)}>
              <Plus className="h-4 w-4" />
              Add a payer
            </Button>
          </div>
        )}
      </div>
    </SettingsCard>
  )
}
