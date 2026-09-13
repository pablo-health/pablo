// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Loader2, Plus, Search } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { usePayerDirectory } from "@/hooks/useCoverage"
import type { PayerDirectoryMatch } from "@/types/coverage"

/**
 * Add a payer by finding it, rather than by knowing its code.
 *
 * The old form was two free-text boxes: a name she invents and a payer id she
 * has to know. `60054` is Aetna, and nobody knows that from memory — so the id
 * got looked up somewhere else, or typed wrong and discovered when an
 * enrollment was filed against a payer that does not exist.
 *
 * The clearinghouse directory is the authority on which payers exist, so she
 * searches it and picks. Name and id both come from the entry she chose, which
 * means they cannot disagree, and two practices naming one insurer differently
 * stops happening.
 *
 * Each row says what that payer will require an enrollment for, from the
 * directory's own `transactionSupport`. Told here it is information; told after
 * she commits it is a surprise.
 *
 * Typing it by hand stays available. The directory is authoritative about what
 * it knows, not about what exists — a practice whose payer is genuinely missing
 * must not be stuck, the same way the NPI field accepts a code we have never
 * heard of.
 */

const TRANSACTION_LABELS: Record<string, string> = {
  "837P": "claims",
  "270": "eligibility",
  "835": "remittance",
}

/**
 * The vendor's timeframe vocabulary, in words a therapist can plan around.
 *
 * A transaction the directory says nothing about is simply absent from the
 * map and gets no line. Silence is not "quick", and guessing in the
 * reassuring direction is how somebody books a client against a connection
 * that is not live yet.
 */
const TIMEFRAME_LABELS: Record<string, string> = {
  INSTANT: "straight away",
  HOURS: "within hours",
  DAYS: "a few days",
  WEEKS: "a few weeks",
  OVER_4_WEEKS: "over a month",
}

const TIMEFRAME_RANK = ["INSTANT", "HOURS", "DAYS", "WEEKS", "OVER_4_WEEKS"]

function enrollmentSummary(required: string[]): string {
  if (required.length === 0) return "No enrollment needed"
  const named = required.map((t) => TRANSACTION_LABELS[t] ?? t)
  return `Needs enrollment for ${named.join(", ")}`
}

/**
 * The longest wait this payer will put her through, as one line.
 *
 * The slowest rather than a list: what she is planning around is when
 * everything is working, and that is set by the last one to answer.
 */
function slowestAnswer(match: PayerDirectoryMatch): string | null {
  let worst: { tx: string; frame: string } | null = null
  for (const [tx, frame] of Object.entries(match.answer_timeframes ?? {})) {
    if (!worst || TIMEFRAME_RANK.indexOf(frame) > TIMEFRAME_RANK.indexOf(worst.frame)) {
      worst = { tx, frame }
    }
  }
  if (!worst) return null
  const label = TIMEFRAME_LABELS[worst.frame]
  if (!label) return null
  return `${TRANSACTION_LABELS[worst.tx] ?? worst.tx} takes ${label}`
}

export function AddPayerFromDirectory({
  onPick,
  onManual,
  onCancel,
  pending,
}: {
  onPick: (match: PayerDirectoryMatch) => void
  onManual: (name: string, payerId: string) => void
  onCancel: () => void
  pending?: boolean
}) {
  const [term, setTerm] = useState("")
  const [query, setQuery] = useState<string | null>(null)
  const [byHand, setByHand] = useState(false)
  const [manualName, setManualName] = useState("")
  const [manualId, setManualId] = useState("")

  const { data, isLoading } = usePayerDirectory(query)

  function search() {
    if (!term.trim()) return
    setQuery(term.trim())
  }

  if (byHand) {
    return (
      <div className="mt-2 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <div className="grid gap-1.5">
          <Label htmlFor="new-payer-name">Name</Label>
          <Input
            id="new-payer-name"
            value={manualName}
            onChange={(e) => setManualName(e.target.value)}
            placeholder="e.g. Aetna"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="new-payer-id">Payer ID</Label>
          <Input
            id="new-payer-id"
            value={manualId}
            onChange={(e) => setManualId(e.target.value)}
            placeholder="e.g. 60054"
          />
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => onManual(manualName.trim(), manualId.trim())}
            disabled={pending || !manualName.trim() || !manualId.trim()}
          >
            Add
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setByHand(false)}>
            Back to search
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="mt-2 space-y-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
        <div className="grid gap-1.5">
          <Label htmlFor="payer-search">Find your insurer</Label>
          <Input
            id="payer-search"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
            placeholder="e.g. Aetna"
          />
        </div>
        <Button type="button" size="sm" onClick={search} disabled={!term.trim() || isLoading}>
          {isLoading ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Search className="mr-1 h-4 w-4" aria-hidden />
          )}
          Search
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>

      {/* A vendor outage and "no such payer" are different answers and send her
          to different places, so they never share a message. */}
      {data?.unavailable && (
        <p className="text-sm text-amber-800">
          The payer directory isn&rsquo;t answering right now. You can still add one
          by hand and we&rsquo;ll check it later.
        </p>
      )}

      {data !== undefined && !data.unavailable && data.matches.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing matched. Insurers are often listed under a longer legal name
          than the one on the card &mdash; try part of it.
        </p>
      )}

      {data !== undefined && data.matches.length > 0 && (
        <ul aria-label="Payer search results" className="m-0 list-none space-y-2 p-0">
          {data.matches.map((match) => (
            <li
              key={match.payer_id}
              className="flex items-center justify-between gap-4 rounded-xl border border-border bg-background p-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{match.name}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Payer ID {match.payer_id} &middot; {enrollmentSummary(match.requires_enrollment)}
                  {slowestAnswer(match) && <> &middot; {slowestAnswer(match)}</>}
                </p>
                {/* The one consequence she cannot undo by unticking a box
                    later: with a TIN-level payer, enrolling reroutes every
                    NPI billing under her tax id. Invisible solo; a colleague's
                    bad week in a group practice. Said before she picks, because
                    afterwards it is somebody else's problem to discover. */}
                {(match.moves_whole_tax_id?.length ?? 0) > 0 && (
                  <p className="mt-1 text-xs text-amber-800">
                    This payer enrolls everyone billing under your tax ID, not
                    just you &mdash; colleagues sharing it will have their{" "}
                    {(match.moves_whole_tax_id ?? [])
                      .map((t) => TRANSACTION_LABELS[t] ?? t)
                      .join(" and ")}{" "}
                    routed here too.
                  </p>
                )}
                {match.ptan_required && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Wants your PTAN when you enroll.
                  </p>
                )}
              </div>
              {match.already_added ? (
                <span className="shrink-0 text-xs text-muted-foreground">Already added</span>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending}
                  onClick={() => onPick(match)}
                >
                  <Plus className="mr-1 h-4 w-4" aria-hidden />
                  Add
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={() => setByHand(true)}
        className="border-0 bg-transparent p-0 text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
      >
        My insurer isn&rsquo;t listed
      </button>
    </div>
  )
}
