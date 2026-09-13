// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check, Loader2, Pencil, Search } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SetupStepHead } from "@/components/setup"
import { useSettingsUserStatus } from "@/components/settings/useSettingsPreferences"
import { useNpiLookup } from "@/hooks/useCredentialingChecklist"
import { NoNpiYet } from "./NoNpiYet"
import { NpiNameSearch } from "./NpiNameSearch"

/**
 * Where credentialing starts: one number, and everything else follows from it.
 *
 * The tiered checklist is the right shape for the work and the wrong shape for
 * the first screen — opening on three tiers and fourteen cards reads as a form
 * to survive rather than a thing to do. This asks for one fact she knows by
 * heart, and then does the typing for her.
 *
 * If her NPI is already on the record from onboarding, the lookup runs on
 * arrival and she never types anything: the screen becomes "is this you?",
 * which is a question, not a form.
 *
 * Four outcomes, and they are genuinely different screens rather than shades
 * of an error:
 *
 * * **found** — here is what the registry says; confirm it or fix it.
 * * **not found** — the number is probably mistyped. Ten digits are easy to
 *   get wrong and the registry is authoritative about what exists, so the
 *   honest thing is to ask her to check it rather than to imply she is not a
 *   real provider.
 * * **unavailable** — CMS is down. Nothing is wrong with her or with us, and
 *   she can carry on; the lookup is a convenience, never a gate.
 * * **no NPI on file** — ask for it.
 *
 * Nothing here writes to her record. The registry's answer is presented, and
 * confirming it is what promotes it — the same order the Tier-0 cards use, and
 * the reason a lookup that wrote directly would quietly undo the design.
 */
export function NpiLookupStep({ onConfirmed }: { onConfirmed?: (npi: string) => void }) {
  const { data: user, isLoading: userLoading } = useSettingsUserStatus()
  const knownNpi = user?.npi_number ?? null

  const [draft, setDraft] = useState("")
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  // Three doors, because three different people arrive here: she knows it, she
  // cannot recall it, or she does not have one. The old screen offered only a
  // text box, which silently assumed the first two were the only cases.
  const [door, setDoor] = useState<"number" | "name" | "none">("number")

  // Derived, not synced. Her NPI arrives asynchronously, and the obvious shape
  // — an effect that copies it into state once it lands — is a cascading
  // render and a second source of truth for the same fact. What we look up is
  // simply "what she submitted, or failing that what the record already holds",
  // which needs no effect and cannot drift.
  const activeNpi = submitted ?? (editing ? null : knownNpi)

  const { data: lookup, isLoading: lookingUp, error } = useNpiLookup(activeNpi)

  const wellFormed = /^\d{10}$/.test(draft.trim())
  const showForm = activeNpi === null || editing || (lookup !== undefined && !lookup.found)

  function search() {
    if (!wellFormed) return
    setSubmitted(draft.trim())
    setEditing(false)
  }

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Credentialing"
        title="Let's start with your NPI"
        lede="Payers identify you by it, and the public registry already holds most of what an application asks for. We'll look it up so you don't have to type it."
      />

      {userLoading && submitted === null ? (
        <p className="text-sm text-stone-500">Checking what we already have…</p>
      ) : null}

      {showForm && (
        <div className="rounded-xl border border-stone-200 bg-white p-4">
          <label htmlFor="npi" className="text-sm font-medium text-stone-900">
            Your individual NPI
          </label>
          <p className="mt-1 text-xs text-stone-600">
            Ten digits. This is your own NPI, not your practice&rsquo;s.
          </p>
          <div className="mt-3 flex items-end gap-2">
            <Input
              id="npi"
              inputMode="numeric"
              maxLength={10}
              placeholder="1999999984"
              value={draft}
              onChange={(e) => setDraft(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && search()}
              className="max-w-[14rem]"
            />
            <Button type="button" onClick={search} disabled={!wellFormed || lookingUp}>
              {lookingUp ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Search className="mr-1 h-4 w-4" aria-hidden />
              )}
              Look it up
            </Button>
          </div>

          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            <button
              type="button"
              onClick={() => setDoor("name")}
              className="border-0 bg-transparent p-0 text-stone-700 underline underline-offset-4 hover:text-stone-900"
            >
              I don&rsquo;t know my NPI
            </button>
            <button
              type="button"
              onClick={() => setDoor("none")}
              className="border-0 bg-transparent p-0 text-stone-700 underline underline-offset-4 hover:text-stone-900"
            >
              I don&rsquo;t have one
            </button>
          </div>
        </div>
      )}

      {showForm && door === "name" && (
        <NpiNameSearch
          onPick={(npi) => {
            setDraft(npi)
            setSubmitted(npi)
            setEditing(false)
            setDoor("number")
          }}
        />
      )}

      {showForm && door === "none" && <NoNpiYet />}

      {lookingUp && !showForm && (
        <p className="flex items-center gap-2 text-sm text-stone-600">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Looking up {activeNpi} in the NPI registry…
        </p>
      )}

      {/* An outage is not her problem and must not read as one. */}
      {error !== null && error !== undefined && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
          <p className="text-sm font-medium text-amber-900">
            The NPI registry isn&rsquo;t answering right now
          </p>
          <p className="mt-1 text-sm text-amber-800">
            Nothing is wrong on your end. You can carry on and we&rsquo;ll try again
            later — the lookup only saves you typing.
          </p>
        </div>
      )}

      {lookup !== undefined && !lookup.found && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
          <p className="text-sm font-medium text-amber-900">
            We couldn&rsquo;t find {lookup.npi} in the registry
          </p>
          <p className="mt-1 text-sm text-amber-800">
            Ten digits are easy to mistype. Have another look at the number — if
            it is right, carry on and we&rsquo;ll sort it out with the payer.
          </p>
        </div>
      )}

      {lookup !== undefined && lookup.found && !editing && (
        <div className="rounded-xl border border-stone-200 bg-white p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
            From the NPPES registry
          </p>
          <p className="mt-2 text-base font-medium text-stone-900">
            {lookup.legal_name ?? "No name on file"}
            {lookup.credential ? `, ${lookup.credential}` : ""}
          </p>
          <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            <Fact label="NPI" value={lookup.npi} />
            <Fact
              label="Taxonomy"
              value={
                lookup.taxonomy_code
                  ? `${lookup.taxonomy_code}${
                      lookup.taxonomy_description ? ` · ${lookup.taxonomy_description}` : ""
                    }`
                  : null
              }
            />
            <Fact
              label="Licence"
              value={
                lookup.license_number
                  ? `${lookup.license_number}${
                      lookup.license_state ? ` (${lookup.license_state})` : ""
                    }`
                  : null
              }
            />
            <Fact label="Practice address" value={lookup.address_line1} />
            <Fact
              label="City"
              value={
                [lookup.city, lookup.state, lookup.postal_code].filter(Boolean).join(", ") || null
              }
            />
          </dl>

          {lookup.entity_type === 2 && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              That&rsquo;s an organisation&rsquo;s NPI rather than a person&rsquo;s. Your
              practice has one of those too, but payers identify <em>you</em> by your
              individual NPI &mdash; it&rsquo;s worth checking you&rsquo;ve got the right one.
            </p>
          )}
          {!lookup.active && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              The registry has this registration as deactivated. If it should be
              active, CMS is who reactivates it.
            </p>
          )}

          <div className="mt-4 flex items-center gap-2">
            <Button type="button" size="sm" onClick={() => onConfirmed?.(lookup.npi)}>
              <Check className="mr-1 h-4 w-4" aria-hidden />
              That&rsquo;s me
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(true)
                setDraft(lookup.npi)
              }}
            >
              <Pencil className="mr-1 h-4 w-4" aria-hidden />
              That&rsquo;s not me
            </Button>
          </div>
          <p className="mt-3 text-xs text-stone-500">
            Confirming copies these onto your record. We never write what the
            registry says until you say it is right.
          </p>
        </div>
      )}
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs text-stone-500">{label}</dt>
      <dd className={value ? "text-stone-900" : "text-stone-400"}>
        {value ?? "Nothing on file"}
      </dd>
    </div>
  )
}
