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
import type { AppointmentTypeResponse } from "@/types/scheduling"

export const NO_TYPES_HELP =
  "Create an appointment type under Scheduling first. A booking link books one type."

/**
 * The one control a booking link needs: which appointment type it books.
 * Length is not asked for because the type already answers it.
 */
export function AppointmentTypeSelect({
  id,
  types,
  value,
  onChange,
}: {
  id: string
  types: AppointmentTypeResponse[]
  value: string | null
  onChange: (id: string) => void
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>Appointment type</Label>
      {types.length === 0 ? (
        <p className="text-sm text-neutral-600">{NO_TYPES_HELP}</p>
      ) : (
        <Select value={value ?? undefined} onValueChange={onChange}>
          <SelectTrigger id={id} className="w-64">
            <SelectValue placeholder="Pick a type" />
          </SelectTrigger>
          <SelectContent>
            {types.map((type) => (
              <SelectItem key={type.id} value={type.id}>
                {type.name} · {type.duration_minutes} min
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  )
}
