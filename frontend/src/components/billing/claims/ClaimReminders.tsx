// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the practice's claims still need a person to do.
 *
 * These used to appear on the compliance dashboard, filed as ``claim_*``
 * compliance items beside a licence renewal and a CAQH attestation. Convenient
 * to look at, wrong underneath: a compliance item is about the clinician and
 * comes round again on a cadence, while a rejection is about one client's claim
 * and ends when that claim moves. Sharing one table cost a foreign key and a
 * unique constraint, and their absence filed duplicate reminders.
 *
 * So they live here now, with the rest of the claims work.
 *
 * Two things this screen deliberately does not do:
 *
 * 1. **No count when there is nothing.** A zero here would be a badge that is
 *    almost always zero, which teaches people to stop reading it. The section
 *    is simply absent until a claim needs something.
 * 2. **Completing does not touch the claim.** It says a person has dealt with
 *    it. The claim moves when the payer moves it, and a screen that implied
 *    otherwise would have somebody marking a denial done and expecting it
 *    filed.
 */

"use client"

import { useState } from "react"
import { AlertTriangle, Check, Loader2 } from "lucide-react"
import { useClaimReminders, useCompleteClaimReminder } from "@/hooks/useClaims"
import type { ClaimReminder } from "@/types/claims"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

/**
 * The deadline line, or nothing.
 *
 * "Overdue" is said plainly rather than in red alone, because a colour is not
 * a sentence and this is the one state where being wrong costs money.
 */
function dueLine(reminder: ClaimReminder): string | null {
  if (!reminder.due_date) return null
  const due = new Date(`${reminder.due_date}T00:00:00`)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const formatted = due.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
  return due < today ? `Was due ${formatted}` : `Due ${formatted}`
}

function ReminderCard({ reminder }: { reminder: ClaimReminder }) {
  const complete = useCompleteClaimReminder()
  const [error, setError] = useState<string | null>(null)
  const due = dueLine(reminder)

  return (
    <li
      className="rounded-lg border border-border p-4 space-y-2"
      data-testid="claim-reminder"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="font-medium text-neutral-900">{reminder.label}</span>
        {due && <span className="text-sm text-muted-foreground">{due}</span>}
      </div>

      {/* The payer's own words, when it gave any. Whitespace preserved because
          the codes and the instructions arrive as separate lines. */}
      {reminder.notes && (
        <p className="whitespace-pre-line text-sm text-neutral-700">{reminder.notes}</p>
      )}

      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          disabled={complete.isPending}
          onClick={() => {
            setError(null)
            complete.mutate(reminder.id, {
              onError: () => setError("That didn’t go through. Nothing changed; try again."),
            })
          }}
        >
          {complete.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Check className="mr-2 h-4 w-4" />
          )}
          Mark done
        </Button>
        {error && <span className="text-sm text-muted-foreground">{error}</span>}
      </div>
    </li>
  )
}

export function ClaimReminders() {
  const { data, isLoading, isError } = useClaimReminders()

  if (isLoading) {
    return <Skeleton className="h-24 w-full" />
  }

  // A failure must say so rather than render as "nothing to do". The two look
  // identical and only one of them means the practice can stop looking.
  if (isError || !data) {
    return (
      <p className="text-sm text-muted-foreground">
        We couldn&rsquo;t load what your claims need just now. Nothing is lost; try again in
        a moment.
      </p>
    )
  }

  if (data.data.length === 0) return null

  return (
    <section className="space-y-3" data-testid="claim-reminders">
      <h2 className="flex items-center gap-2 font-display text-lg font-semibold text-neutral-900">
        <AlertTriangle className="h-5 w-5 text-honey-600" aria-hidden />
        {data.data.length === 1
          ? "A claim needs you"
          : `${data.data.length} claims need you`}
      </h2>
      <ul className="space-y-3">
        {data.data.map((reminder: ClaimReminder) => (
          <ReminderCard key={reminder.id} reminder={reminder} />
        ))}
      </ul>
    </section>
  )
}
