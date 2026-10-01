// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Offered when an hours sentence names a kind of appointment the practice
 * does not have ("only two groups a week" with no Group type). Creating it
 * here, then reading the sentence again, beats sending the clinician off to
 * another page and back.
 *
 * Only what availability needs is asked: the name, how long it runs, and
 * whether it is for new clients. Fee and service code belong to billing and
 * stay unset until the clinician adds them there.
 */

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCreateAppointmentType } from "@/hooks/useAppointmentTypes"

const NEW_CLIENT_WORDS = /\b(intake|consult|evaluation|eval|assessment|new)/i

function defaultDuration(name: string): number {
  return NEW_CLIENT_WORDS.test(name) ? 60 : 50
}

interface MissingAppointmentTypeOfferProps {
  /** The kind of appointment the sentence named, as the parser read it. */
  name: string
  /** The type exists now; read the sentence again. */
  onCreated: () => void
  /** Leave the sentence as it is. */
  onDismiss?: () => void
}

export function MissingAppointmentTypeOffer({
  name,
  onCreated,
  onDismiss,
}: MissingAppointmentTypeOfferProps) {
  const create = useCreateAppointmentType()
  const [typeName, setTypeName] = useState(name)
  const [duration, setDuration] = useState(String(defaultDuration(name)))
  const [forNewClients, setForNewClients] = useState(NEW_CLIENT_WORDS.test(name))

  const minutes = Number(duration)
  const valid = typeName.trim().length > 0 && Number.isInteger(minutes) && minutes >= 5 && minutes <= 480

  function handleCreate() {
    if (!valid) return
    create.mutate(
      {
        name: typeName.trim(),
        duration_minutes: minutes,
        audience: forNewClients ? "new" : "existing",
      },
      { onSuccess: () => onCreated() },
    )
  }

  return (
    <div
      className="space-y-3 rounded-md border border-neutral-200 bg-neutral-50 p-3"
      data-testid="missing-appointment-type-offer"
    >
      <p className="text-sm text-neutral-800">
        You don&apos;t have a &ldquo;{name}&rdquo; appointment type yet. Add it, and Pablo will
        read your sentence again.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <Label htmlFor="missing-type-name">Name</Label>
          <Input
            id="missing-type-name"
            value={typeName}
            onChange={(e) => setTypeName(e.target.value)}
            maxLength={100}
            className="w-48"
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="missing-type-duration">Minutes</Label>
          <Input
            id="missing-type-duration"
            type="number"
            inputMode="numeric"
            min={5}
            max={480}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            className="w-24"
          />
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm text-neutral-800">
          <input
            type="checkbox"
            checked={forNewClients}
            onChange={(e) => setForNewClients(e.target.checked)}
          />
          For new clients
        </label>
      </div>
      <div className="flex gap-2">
        <Button size="sm" onClick={handleCreate} disabled={!valid || create.isPending}>
          {create.isPending ? "Adding..." : `Add ${typeName.trim() || "type"}`}
        </Button>
        {onDismiss && (
          <Button size="sm" variant="ghost" onClick={onDismiss} disabled={create.isPending}>
            Not now
          </Button>
        )}
      </div>
      {create.isError && (
        <p role="alert" className="text-sm text-red-600">
          That appointment type could not be added. Try again, or add it under Scheduling.
        </p>
      )}
    </div>
  )
}
