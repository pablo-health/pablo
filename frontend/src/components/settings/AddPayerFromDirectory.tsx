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

function enrollmentSummary(required: string[]): string {
  if (required.length === 0) return "No enrollment needed"
  const named = required.map((t) => TRANSACTION_LABELS[t] ?? t)
  return `Needs enrollment for ${named.join(", ")}`
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
                </p>
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
