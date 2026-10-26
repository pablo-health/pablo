// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "How to work with <client>": what the practice knows about scheduling,
 * communicating with and looking after this client, on their chart. Anyone
 * who can open the client sees the same list.
 *
 * Preferred name and pronouns are the client's own and are only shown here;
 * they are edited on the client's details. Kinds this screen has no editor for
 * (a deployment may register more) are listed read-only. Removing a row
 * retires it: the record keeps it, the chart stops showing it.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { usePatientPreferences, useRetirePreference } from "@/hooks/usePatientPreferences"
import { isCoreKind, type Preference } from "@/types/patientPreferences"
import { PreferenceDialog } from "./PreferenceDialog"
import {
  ADD,
  DOMAIN_LABELS,
  EDIT,
  EMPTY,
  RETIRE,
  RETIRE_FAILED,
  heading,
  identityLine,
  preferenceLine,
  provenance,
  wordsLine,
} from "./preferencesCopy"

interface ClientPreferencesProps {
  patientId: string
  /** What the client is called: their preferred name, else their first name. */
  name: string
  preferredName?: string | null
  pronouns?: string | null
}

function byDomain(rows: Preference[]): [string, Preference[]][] {
  const groups = new Map<string, Preference[]>()
  for (const row of rows) groups.set(row.domain, [...(groups.get(row.domain) ?? []), row])
  return [...groups.entries()]
}

export function ClientPreferences({ patientId, name, preferredName, pronouns }: ClientPreferencesProps) {
  const { data } = usePatientPreferences(patientId)
  const retire = useRetirePreference()
  // `undefined` while closed; `null` to add one.
  const [editing, setEditing] = useState<Preference | null | undefined>(undefined)
  const [failure, setFailure] = useState<string | null>(null)
  const rows = data?.data ?? []
  const identity = identityLine(preferredName, pronouns)

  async function remove(preference: Preference) {
    setFailure(null)
    try {
      await retire.mutateAsync({ patientId, preferenceId: preference.id })
    } catch {
      setFailure(RETIRE_FAILED)
    }
  }

  return (
    <section aria-label={heading(name)} className="card" data-testid="client-preferences">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-display font-semibold text-neutral-900">{heading(name)}</h2>
        <Button variant="outline" size="sm" onClick={() => setEditing(null)}>
          {ADD}
        </Button>
      </div>
      {identity && <p className="mt-1 text-sm text-neutral-600">{identity}</p>}

      {data && rows.length === 0 && <p className="mt-3 text-sm text-neutral-500">{EMPTY}</p>}

      {byDomain(rows).map(([domain, group]) => (
        <div key={domain} className="mt-3">
          <h3 className="text-xs font-medium uppercase tracking-wide text-neutral-500">
            {DOMAIN_LABELS[domain] ?? domain}
          </h3>
          <ul className="mt-1 space-y-1.5" role="list">
            {group.map((preference) => (
              <li
                key={preference.id}
                data-testid={`preference-${preference.id}`}
                className="flex items-start justify-between gap-3"
              >
                <div className="min-w-0">
                  <p className="text-sm text-neutral-900">{preferenceLine(preference)}</p>
                  {wordsLine(preference) && (
                    <p className="text-sm text-neutral-600">{wordsLine(preference)}</p>
                  )}
                  <p className="text-xs text-neutral-500">{provenance(preference)}</p>
                </div>
                {isCoreKind(preference.kind) && (
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(preference)}>
                      {EDIT}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => void remove(preference)}>
                      {RETIRE}
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}

      {failure && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {failure}
        </p>
      )}

      {editing !== undefined && (
        <PreferenceDialog
          patientId={patientId}
          name={name}
          preference={editing}
          existing={rows}
          open
          onOpenChange={(open) => !open && setEditing(undefined)}
        />
      )}
    </section>
  )
}
