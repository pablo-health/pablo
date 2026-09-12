// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check, Pencil } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { Confirmation, ConfirmationPayload, IntakeField } from "@/types/credentialing"
import { sourceLabel } from "./tiers"

interface ConfirmCardProps {
  field: IntakeField
  /** What she has already said about this field, if anything. */
  confirmation?: Confirmation
  /** The value we are showing her, already looked up. */
  presentedValue: string | null
  onRecord: (fieldKey: string, payload: ConfirmationPayload) => void
  pending?: boolean
}

/**
 * One pre-filled value, with where it came from and two ways to answer.
 *
 * Never an empty input. This tier asks nothing — the value is already known,
 * and her job is to say whether it is right. "That's not right" opens a box
 * for the correction, because a value marked wrong with nothing beside it
 * records only that a public source disagrees with her, not what it should say.
 */
export function ConfirmCard({
  field,
  confirmation,
  presentedValue,
  onRecord,
  pending = false,
}: ConfirmCardProps) {
  const [correcting, setCorrecting] = useState(false)
  const [correction, setCorrection] = useState(confirmation?.correction ?? "")

  const answered = confirmation !== undefined
  const shown = presentedValue ?? confirmation?.presented_value ?? null

  function confirm() {
    onRecord(field.key, {
      source: field.source ?? "clinician_profiles",
      confirmed: true,
      presented_value: shown,
    })
    setCorrecting(false)
  }

  function submitCorrection() {
    if (!correction.trim()) return
    onRecord(field.key, {
      source: field.source ?? "clinician_profiles",
      confirmed: false,
      presented_value: shown,
      correction: correction.trim(),
    })
    setCorrecting(false)
  }

  return (
    <div
      className={`rounded-xl border p-4 transition-colors ${
        answered ? "border-emerald-200 bg-emerald-50/40" : "border-stone-200 bg-white"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-stone-900">{field.label}</p>
          <p className="mt-0.5 truncate text-base text-stone-700">
            {shown ?? <span className="text-stone-400">Nothing on file</span>}
          </p>
          <p className="mt-1 text-xs text-stone-500">
            From {sourceLabel(field.source)}
            {field.help_text ? ` · ${field.help_text}` : ""}
          </p>
        </div>

        {!correcting && (
          <div className="flex shrink-0 items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant={confirmation?.confirmed ? "secondary" : "default"}
              disabled={pending}
              onClick={confirm}
            >
              <Check className="mr-1 h-4 w-4" aria-hidden />
              {confirmation?.confirmed ? "Confirmed" : "Looks right"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => setCorrecting(true)}
            >
              <Pencil className="mr-1 h-4 w-4" aria-hidden />
              Not right
            </Button>
          </div>
        )}
      </div>

      {correcting && (
        <div className="mt-3 flex items-end gap-2">
          <div className="flex-1">
            <label
              htmlFor={`correction-${field.key}`}
              className="text-xs font-medium text-stone-600"
            >
              What should it say?
            </label>
            <Input
              id={`correction-${field.key}`}
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
              placeholder="The correct value"
              className="mt-1"
            />
          </div>
          <Button type="button" size="sm" disabled={pending} onClick={submitCorrection}>
            Save
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setCorrecting(false)}
          >
            Cancel
          </Button>
        </div>
      )}

      {confirmation && !confirmation.confirmed && !correcting && (
        <p className="mt-2 text-xs text-amber-700">
          You told us this should be “{confirmation.correction}”. We will use that.
        </p>
      )}
    </div>
  )
}
