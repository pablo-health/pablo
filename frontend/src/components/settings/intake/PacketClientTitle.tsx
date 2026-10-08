// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useUpdateIntakeTemplate } from "@/hooks/useIntakePackets"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { IntakeTemplate } from "@/types/intakePackets"
import { CLIENT_TITLE_HINT, SAVE_TITLE, clientTitleLabel } from "./intakeCopy"

/**
 * The title a person sees for this packet in their portal, beside the
 * practice's own name for it.
 *
 * Optional: empty is a real answer, and saving it empty clears the title so
 * the portal uses its own wording. Save shows once the title has changed.
 */
export function PacketClientTitle({ template }: { template: IntakeTemplate }) {
  const people = usePeopleTerm()
  const update = useUpdateIntakeTemplate()
  const saved = template.client_title ?? ""
  const [title, setTitle] = useState(saved)
  const id = `packet-client-title-${template.id}`

  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="space-y-1">
        <Label htmlFor={id} className="text-[12.5px] font-semibold">
          {clientTitleLabel(people)}
        </Label>
        <Input
          id={id}
          className="h-8 w-64 text-[13px]"
          maxLength={120}
          value={title}
          aria-describedby={`${id}-hint`}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      {title.trim() !== saved && (
        <Button
          type="button"
          size="sm"
          onClick={() =>
            update.mutate({ id: template.id, data: { client_title: title.trim() || null } })
          }
          disabled={update.isPending}
        >
          {SAVE_TITLE}
        </Button>
      )}
      <p id={`${id}-hint`} className="w-full text-[12px] text-muted-foreground">
        {CLIENT_TITLE_HINT}
      </p>
      {update.error && (
        <p role="alert" className="w-full text-[12px] text-red-700">
          {update.error instanceof Error && update.error.message
            ? update.error.message
            : "That could not be saved."}
        </p>
      )}
    </div>
  )
}
