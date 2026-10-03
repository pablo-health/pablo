// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  usePeopleTermState,
  useSetPeopleTerm,
  useSetPracticePeopleTerm,
} from "@/hooks/usePeopleTerm"
import type { PeopleTerm } from "@/lib/peopleTerm"

// "Not set" for the practice default. Radix Select can't hold an empty value.
const UNSET = "unset"

const WORDS: { value: PeopleTerm; label: string }[] = [
  { value: "clients", label: "Clients" }, // people-term-ok: the choice itself
  { value: "patients", label: "Patients" }, // people-term-ok: the choice itself
]

/**
 * Whether the app says "clients" or "patients".
 *
 * The clinician's own control shows the word in use, whatever decided it, so
 * picking the one already shown changes nothing they can see. The practice
 * default is the owner's, and only reaches a clinician whose license doesn't
 * settle it and who hasn't chosen.
 */
export function PeopleTermSettings() {
  const { data } = usePeopleTermState()
  const setOwn = useSetPeopleTerm()
  const setPractice = useSetPracticePeopleTerm()

  if (!data) return null

  return (
    <div className="space-y-4 max-w-sm">
      <div className="space-y-2">
        <Label htmlFor="people-term">What Pablo calls the people you see</Label>
        <Select
          value={data.people_term}
          onValueChange={(v) => setOwn.mutate(v as PeopleTerm)}
          disabled={setOwn.isPending}
        >
          <SelectTrigger id="people-term">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {WORDS.map((w) => (
              <SelectItem key={w.value} value={w.value}>
                {w.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {data.can_set_practice_default && (
        <div className="space-y-2">
          <Label htmlFor="practice-people-term">Practice default</Label>
          <Select
            value={data.practice_default ?? UNSET}
            onValueChange={(v) => setPractice.mutate(v === UNSET ? null : (v as PeopleTerm))}
            disabled={setPractice.isPending}
          >
            <SelectTrigger id="practice-people-term">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={UNSET}>Not set</SelectItem>
              {WORDS.map((w) => (
                <SelectItem key={w.value} value={w.value}>
                  {w.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-sm text-neutral-600">
            For clinicians whose license doesn&rsquo;t settle it. Anyone can still choose their own.
          </p>
        </div>
      )}

      {(setOwn.isError || setPractice.isError) && (
        <p className="text-sm text-red-600">Failed to save. Please try again.</p>
      )}
    </div>
  )
}
