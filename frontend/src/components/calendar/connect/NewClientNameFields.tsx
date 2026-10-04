// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Input } from "@/components/ui/input"
import type { SuggestedName } from "@/lib/api/scheduling"

/** A new client's name as the clinician has it so far. */
export interface NewClientName {
  first_name: string
  last_name: string
}

const EMPTY: NewClientName = { first_name: "", last_name: "" }

/**
 * The name a new-client row shows: what the clinician typed, else what the
 * title plainly gives (worked out by the server, from the same readings its
 * matcher uses), else nothing. Initials and single words give nothing, so
 * those fields start empty.
 */
export function newClientName(
  typed: NewClientName | undefined,
  suggested: SuggestedName | null | undefined
): NewClientName {
  return typed ?? suggested ?? EMPTY
}

/** The answer fields for a new client: the name, trimmed, as typed. Left
 * blank, the chart takes the title's name part and shows as needing a name. */
export function newClientNameFields(name: NewClientName): {
  new_client_first_name: string
  new_client_last_name: string
} {
  return {
    new_client_first_name: name.first_name.trim(),
    new_client_last_name: name.last_name.trim(),
  }
}

/** First and last name inputs for a row about to become a new client, with
 * the calendar's wording beside them when it didn't give both parts. */
export function NewClientNameFields({
  rowKey,
  title,
  name,
  suggested,
  onChange,
}: {
  rowKey: string
  title: string
  name: NewClientName
  suggested: SuggestedName | null | undefined
  onChange: (name: NewClientName) => void
}) {
  const showTitle = !suggested?.first_name || !suggested?.last_name
  const inputClass = "h-7 px-2 text-xs md:text-xs"
  return (
    <div data-testid={`new-client-name-${rowKey}`} className="mt-1.5">
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-muted-foreground">
          First name
          <Input
            value={name.first_name}
            onChange={(event) => onChange({ ...name, first_name: event.target.value })}
            maxLength={255}
            autoComplete="off"
            className={inputClass}
          />
        </label>
        <label className="text-xs text-muted-foreground">
          Last name
          <Input
            value={name.last_name}
            onChange={(event) => onChange({ ...name, last_name: event.target.value })}
            maxLength={255}
            autoComplete="off"
            className={inputClass}
          />
        </label>
      </div>
      {showTitle ? (
        <span className="mt-1 block text-xs text-muted-foreground">On the calendar: {title}</span>
      ) : null}
    </div>
  )
}
