// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Loader2, Search } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useNpiSearch } from "@/hooks/useCredentialingChecklist"
import type { NppesMatch, NppesSearchQuery } from "@/types/credentialing"

/**
 * Find her in the registry by name, for when ten digits will not come.
 *
 * "State" here is the registry's two-letter code, which covers the territories
 * and the military posts as well as the fifty states — PR, GU, VI, AS, MP, DC
 * and AE all return providers. The field says so, because a therapist in San
 * Juan has no reason to assume a box labelled "state" wants her.
 *
 * State is not decoration. Measured against the live registry, a common name
 * returns dozens of people nationally and one within a state — "Amanda
 * Nicholson" is five people in five states, three of them mental health.
 *
 * Deliberately NOT filtered to psychology. The registry spreads mental health
 * across Psych*, Social Worker*, Counselor* and Marriage & Family Therapist, so
 * a psych filter hides most LCSWs, LPCs and LMFTs — most therapists. Every row
 * shows its credential, taxonomy and city instead, which is what actually lets
 * her pick herself out of a list of namesakes.
 */
export function NpiNameSearch({ onPick }: { onPick: (npi: string) => void }) {
  const [lastName, setLastName] = useState("")
  const [firstName, setFirstName] = useState("")
  const [state, setState] = useState("")
  const [query, setQuery] = useState<NppesSearchQuery | null>(null)

  const { data, isLoading, error } = useNpiSearch(query)

  function run() {
    if (!lastName.trim()) return
    setQuery({
      last_name: lastName.trim(),
      first_name: firstName.trim() || undefined,
      state: state.trim() || undefined,
    })
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-stone-200 bg-white p-4">
        <p className="text-sm font-medium text-stone-900">Find yourself by name</p>
        <p id="npi-state-help" className="mt-1 text-xs text-stone-600">
          Your state narrows it a lot &mdash; a common name is dozens of people
          nationally and usually one in a single state. Territories work too:
          PR, GU, VI, AS, MP, and DC.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_6rem_auto] sm:items-end">
          <label className="grid gap-1 text-xs font-medium text-stone-700">
            First name
            <Input value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </label>
          <label className="grid gap-1 text-xs font-medium text-stone-700">
            Last name
            <Input
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </label>
          <label className="grid gap-1 text-xs font-medium text-stone-700">
            State or territory
            <Input
              value={state}
              maxLength={2}
              placeholder="NC"
              aria-describedby="npi-state-help"
              onChange={(e) => setState(e.target.value.replace(/[^a-zA-Z]/g, "").toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </label>
          <Button type="button" onClick={run} disabled={!lastName.trim() || isLoading}>
            {isLoading ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Search className="mr-1 h-4 w-4" aria-hidden />
            )}
            Search
          </Button>
        </div>
      </div>

      {error !== null && error !== undefined && (
        <p className="text-sm text-amber-800">
          The registry isn&rsquo;t answering right now. You can carry on without it.
        </p>
      )}

      {data !== undefined && data.matches.length === 0 && (
        <p className="text-sm text-stone-600">
          Nobody matched. Try without a first name &mdash; the registry stores
          the name you enumerated under, which may not be the one you use now.
        </p>
      )}

      {data !== undefined && data.matches.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-stone-500">
            {data.matches.length} {data.matches.length === 1 ? "match" : "matches"}
            {data.truncated ? " (there may be more — add a state to narrow it)" : ""}
          </p>
          <ul className="space-y-2">
            {data.matches.map((match) => (
              <MatchRow key={match.npi} match={match} onPick={onPick} />
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function MatchRow({ match, onPick }: { match: NppesMatch; onPick: (npi: string) => void }) {
  const where = [match.city, match.state].filter(Boolean).join(", ")
  return (
    <li className="flex items-center justify-between gap-4 rounded-xl border border-stone-200 bg-white p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-stone-900">
          {match.legal_name ?? match.npi}
          {match.credential ? `, ${match.credential}` : ""}
        </p>
        <p className="mt-0.5 text-xs text-stone-600">
          {match.taxonomy_description ?? "No taxonomy on file"}
          {where ? ` · ${where}` : ""}
          {" · "}
          {match.npi}
        </p>
        {/* Both are reasons a row is probably not her, and both are quieter
            than hiding the row: the registry is the one making the claim. */}
        {match.entity_type === 2 && (
          <p className="mt-0.5 text-xs text-amber-700">
            This is an organisation&rsquo;s NPI, not a person&rsquo;s.
          </p>
        )}
        {!match.active && (
          <p className="mt-0.5 text-xs text-amber-700">This registration is deactivated.</p>
        )}
      </div>
      <Button type="button" size="sm" variant="outline" onClick={() => onPick(match.npi)}>
        This is me
      </Button>
    </li>
  )
}
