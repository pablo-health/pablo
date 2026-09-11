// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Remittances whose own numbers disagreed, and the two answers to them.
 *
 * A payer states the client's share of a claim twice and the two statements
 * came out different, so the client was not billed. This is where the
 * therapist meets that and settles it.
 *
 * Three things about the design are deliberate and easy to undo by accident:
 *
 * 1. **Both answers are always live.** Neither button is disabled, greyed
 *    out, hidden behind an acknowledgement, or made to wait on anybody
 *    outside the practice. The practice holds the client relationship and
 *    the authority over that balance.
 * 2. **The two figures are shown side by side, not as a delta.** Which side
 *    is which is the whole question — "the payer says $30, the lines say
 *    $0" is actionable and "off by $30" is not.
 * 3. **No amount is presented as the answer.** The stated figure is
 *    labelled as the payer's claim about it, because the entire point of
 *    the hold is that we cannot corroborate it.
 */

"use client"

import { useState } from "react"
import { AlertTriangle, Check, Loader2 } from "lucide-react"
import {
  useAcknowledgeRemittanceHold,
  useRemittanceHolds,
  useResolveRemittanceHold,
} from "@/hooks/useClaims"
import { formatCents } from "@/lib/money"
import type { RemittanceHold, RemittanceHoldReason } from "@/types/claims"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

/** What each check was comparing, in the therapist's terms rather than X12's. */
const REASON_COPY: Record<
  RemittanceHoldReason,
  { label: string; stated: string; computed: string }
> = {
  patient_responsibility: {
    label: "The payer's total for this client does not match its own itemisation",
    stated: "Payer's stated client total",
    computed: "Adds up from the lines to",
  },
  line_balance: {
    label: "A service line's adjustments do not account for what it was paid",
    stated: "Line charged",
    computed: "Paid plus adjustments",
  },
  claim_balance: {
    label: "The claim's adjustments do not account for what it was paid",
    stated: "Claim charged",
    computed: "Paid plus adjustments",
  },
}

function codeList(hold: RemittanceHold): string {
  return hold.codes.map((c) => `${c.group_code}-${c.reason_code}`).join(", ")
}

function HoldCard({ hold }: { hold: RemittanceHold }) {
  const resolve = useResolveRemittanceHold()
  const acknowledge = useAcknowledgeRemittanceHold()
  const [error, setError] = useState<string | null>(null)
  const copy = REASON_COPY[hold.reason]
  const busy = resolve.isPending || acknowledge.isPending

  const decide = (finding: "bill_as_stated" | "waived") => {
    setError(null)
    resolve.mutate(
      { holdId: hold.id, finding },
      { onError: () => setError("That did not go through. Nothing was billed — try again.") },
    )
  }

  return (
    <li
      className="rounded-lg border border-amber-200 bg-amber-50/60 p-4"
      data-testid="remittance-hold"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-3">
          <div>
            <p className="font-medium text-neutral-900">{copy.label}</p>
            <p className="mt-1 text-sm text-neutral-600">
              Claim {hold.control_number}
              {hold.payer_name ? ` · ${hold.payer_name}` : ""}
              {hold.line_control_number ? ` · line ${hold.line_control_number}` : ""}
            </p>
          </div>

          <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <div className="flex justify-between gap-4 sm:block">
              <dt className="text-neutral-600">{copy.stated}</dt>
              <dd className="font-medium tabular-nums text-neutral-900">
                {formatCents(hold.stated_cents)}
              </dd>
            </div>
            <div className="flex justify-between gap-4 sm:block">
              <dt className="text-neutral-600">{copy.computed}</dt>
              <dd className="font-medium tabular-nums text-neutral-900">
                {formatCents(hold.computed_cents)}
              </dd>
            </div>
          </dl>

          {hold.codes.length > 0 && (
            <p className="text-xs text-neutral-500">Adjustment codes: {codeList(hold)}</p>
          )}

          <p className="text-sm text-neutral-700">
            This client has <strong>not</strong> been billed. The payer&rsquo;s payment posted
            normally. Bill {formatCents(hold.patient_responsibility_cents)} as the payer stated,
            or waive it.
          </p>

          {error && (
            <p className="text-sm text-red-700" role="alert">
              {error}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => decide("bill_as_stated")} disabled={busy}>
              {resolve.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              Bill {formatCents(hold.patient_responsibility_cents)} as stated
            </Button>
            <Button size="sm" variant="outline" onClick={() => decide("waived")} disabled={busy}>
              Waive it
            </Button>
            {hold.state === "open" && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => acknowledge.mutate({ holdId: hold.id })}
                disabled={busy}
              >
                I&rsquo;ve seen this
              </Button>
            )}
            {hold.state === "acknowledged" && (
              <span className="inline-flex items-center gap-1 text-xs text-neutral-500">
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
                Seen — still waiting on a decision
              </span>
            )}
          </div>
        </div>
      </div>
    </li>
  )
}

export function RemittanceHolds() {
  const { data, isLoading } = useRemittanceHolds()
  const holds = data?.data ?? []

  if (isLoading) return <Skeleton className="h-32 w-full" />
  // Nothing held is the ordinary state and needs no card of its own; an
  // empty "no problems" panel on every billing screen forever is noise that
  // teaches people to skip the place the real thing will appear.
  if (holds.length === 0) return null

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-lg font-display font-semibold text-neutral-900">
          Remittances that do not add up
        </h2>
        <p className="mt-1 text-sm text-neutral-600">
          A payer stated one of these clients&rsquo; share twice and the two statements disagree,
          so nothing was billed to them. The payment itself posted. Decide what the client owes
          and it will be billed — or waive it.
        </p>
      </div>
      <ul className="space-y-3">
        {holds.map((hold) => (
          <HoldCard key={hold.id} hold={hold} />
        ))}
      </ul>
    </section>
  )
}
