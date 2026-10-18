// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The optional client a task is about. Searching asks the server, so a
 * practice too big for one page of the roster still finds anyone by name.
 * Only clients the clinician can open are listed; the server checks the same
 * thing again when the task is saved.
 */

"use client"

import { useEffect, useState } from "react"
import { X } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { usePatientList } from "@/hooks/usePatients"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

export interface PickedClient {
  id: string
  name: string
}

const SEARCH_DELAY_MS = 250

export function ClientPicker({
  value,
  onChange,
}: {
  value: PickedClient | null
  onChange: (client: PickedClient | null) => void
}) {
  const people = usePeopleTerm()
  const [query, setQuery] = useState("")
  const [search, setSearch] = useState("")
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [query])

  // Nothing is read until the clinician starts typing a name.
  const { data } = usePatientList({ page_size: 20, search }, undefined, {
    enabled: search.length > 0,
  })
  const matches = open && search ? (data?.data ?? []) : []

  if (value) {
    return (
      <div className="space-y-1">
        <Label>{people.One}</Label>
        <div className="flex items-center gap-2" data-testid="task-client">
          <span className="text-sm text-neutral-900">{value.name}</span>
          <button
            type="button"
            onClick={() => onChange(null)}
            className="rounded p-0.5 text-neutral-500 hover:text-neutral-800"
            aria-label={`Remove ${value.name}`}
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="relative space-y-1">
      <Label htmlFor="task-client">{people.One} (optional)</Label>
      <Input
        id="task-client"
        role="combobox"
        aria-expanded={matches.length > 0}
        aria-controls="task-client-options"
        aria-autocomplete="list"
        autoComplete="off"
        placeholder={`Search ${people.many}`}
        value={query}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false)
        }}
      />
      {matches.length > 0 && (
        <ul
          id="task-client-options"
          role="listbox"
          aria-label={people.Many}
          className="absolute z-10 mt-1 max-h-48 w-full overflow-y-auto rounded-md border border-neutral-200 bg-white py-1 shadow-lg"
        >
          {matches.map((p) => {
            const name = `${p.first_name} ${p.last_name}`.trim()
            return (
              <li
                key={p.id}
                role="option"
                aria-selected={false}
                // Before the input's blur closes the list.
                onMouseDown={(e) => {
                  e.preventDefault()
                  onChange({ id: p.id, name })
                  setQuery("")
                  setOpen(false)
                }}
                className="cursor-pointer px-3 py-1.5 text-sm text-neutral-900 hover:bg-neutral-50"
              >
                {name}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
