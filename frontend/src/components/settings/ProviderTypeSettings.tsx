// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { type ProviderType, updateUserProfile } from "@/lib/api/users"

const PROVIDER_TYPES: { value: ProviderType; label: string; description: string }[] = [
  {
    value: "therapist",
    label: "Therapist",
    description: "Provides psychotherapy (LCSW, LMFT, LPC, psychologist, etc.).",
  },
  {
    value: "prescriber",
    label: "Prescriber",
    description: "Manages medications (PMHNP, psychiatrist, etc.).",
  },
  {
    value: "both",
    label: "Both",
    description: "Provides therapy and manages medications.",
  },
]

interface ProviderTypeSettingsProps {
  currentValue: ProviderType | null
  onSaved?: (value: ProviderType) => void
}

export function ProviderTypeSettings({ currentValue, onSaved }: ProviderTypeSettingsProps) {
  const [value, setValue] = useState<ProviderType | null>(currentValue)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (v: ProviderType) => updateUserProfile({ provider_type: v }),
    onSuccess: (_data, v) => {
      queryClient.invalidateQueries({ queryKey: ["user", "status"] })
      onSaved?.(v)
    },
  })

  const handleChange = (v: string) => {
    const next = v as ProviderType
    setValue(next)
    mutation.mutate(next)
  }

  const selected = PROVIDER_TYPES.find((p) => p.value === value)

  return (
    <div className="space-y-2 max-w-sm">
      <Label htmlFor="provider-type">Provider type</Label>
      <Select
        value={value ?? undefined}
        onValueChange={handleChange}
        disabled={mutation.isPending}
      >
        <SelectTrigger id="provider-type">
          <SelectValue placeholder="Select your provider type" />
        </SelectTrigger>
        <SelectContent>
          {PROVIDER_TYPES.map((p) => (
            <SelectItem key={p.value} value={p.value}>
              {p.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selected && (
        <p className="text-sm text-neutral-600">{selected.description}</p>
      )}
      {mutation.isError && (
        <p className="text-sm text-red-600">Failed to save. Please try again.</p>
      )}
    </div>
  )
}
