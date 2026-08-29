// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { useUpdateBookingLink } from "@/hooks/useBookingLinks"
import { ApiError } from "@/lib/api/client"
import type { BookingLink, UpdateBookingLinkRequest } from "@/types/bookingLinks"

const LENGTH_ERROR = "Length must be between 5 and 480 minutes."

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  return "Something went wrong. Please try again."
}

export function BookingLinkEditForm({
  link,
  onCancel,
  onSaved,
}: {
  link: BookingLink
  onCancel: () => void
  onSaved: () => void
}) {
  const updateMutation = useUpdateBookingLink()
  const [hostName, setHostName] = useState(link.host_name)
  const [title, setTitle] = useState(link.title)
  const [description, setDescription] = useState(link.description ?? "")
  const [durationMinutes, setDurationMinutes] = useState(String(link.duration_minutes))
  const [lengthError, setLengthError] = useState<string | null>(null)
  const [serverError, setServerError] = useState<string | null>(null)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setServerError(null)

    const duration = Number(durationMinutes)
    if (!Number.isInteger(duration) || duration < 5 || duration > 480) {
      setLengthError(LENGTH_ERROR)
      return
    }
    setLengthError(null)

    const data: UpdateBookingLinkRequest = {}
    if (hostName.trim() !== link.host_name) data.host_name = hostName.trim()
    if (title.trim() !== link.title) data.title = title.trim()
    if (description !== (link.description ?? "")) data.description = description
    if (duration !== link.duration_minutes) data.duration_minutes = duration

    updateMutation.mutate(
      { linkId: link.id, data },
      {
        onSuccess: onSaved,
        onError: (err) => setServerError(errorMessage(err)),
      }
    )
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-4 rounded-md border border-neutral-200 p-4"
    >
      <div className="grid gap-2">
        <Label>Slug</Label>
        <p className="text-sm text-neutral-900">/book/{link.slug}</p>
        <p className="text-xs text-neutral-500">
          Slugs can&apos;t be changed. Deactivate this link and create a new one if you
          need a different address.
        </p>
      </div>

      <div className="grid gap-2">
        <Label>Session type</Label>
        <p className="text-sm text-neutral-900">{link.session_type}</p>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="edit-link-host-name">Host name</Label>
        <Input
          id="edit-link-host-name"
          value={hostName}
          onChange={(e) => setHostName(e.target.value)}
          required
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="edit-link-title">Title</Label>
        <Input
          id="edit-link-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="edit-link-description">Description</Label>
        <Textarea
          id="edit-link-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="edit-link-duration">Length (minutes)</Label>
        <Input
          id="edit-link-duration"
          type="number"
          value={durationMinutes}
          onChange={(e) => setDurationMinutes(e.target.value)}
          className="w-24"
        />
        {lengthError && (
          <p role="alert" className="text-sm text-red-600">
            {lengthError}
          </p>
        )}
      </div>

      {serverError && (
        <p role="alert" className="text-sm text-red-600">
          {serverError}
        </p>
      )}

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={updateMutation.isPending}>
          {updateMutation.isPending ? "Saving..." : "Save"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
