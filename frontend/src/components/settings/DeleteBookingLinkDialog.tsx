// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useDeleteBookingLink, useUpdateBookingLink } from "@/hooks/useBookingLinks"
import { ApiError } from "@/lib/api/client"
import type { BookingLink } from "@/types/bookingLinks"

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  return "Something went wrong. Please try again."
}

export function DeleteBookingLinkDialog({
  link,
  open,
  onOpenChange,
}: {
  link: BookingLink
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const deleteMutation = useDeleteBookingLink()
  const updateMutation = useUpdateBookingLink()
  const [serverError, setServerError] = useState<string | null>(null)

  function handleDelete() {
    setServerError(null)
    deleteMutation.mutate(link.id, {
      onSuccess: () => onOpenChange(false),
      onError: (err) => {
        if (err instanceof ApiError && err.status === 404) {
          onOpenChange(false)
          return
        }
        setServerError(errorMessage(err))
      },
    })
  }

  function handleDeactivate() {
    setServerError(null)
    updateMutation.mutate(
      { linkId: link.id, data: { is_active: false } },
      {
        onSuccess: () => onOpenChange(false),
        onError: (err) => setServerError(errorMessage(err)),
      }
    )
  }

  const pending = deleteMutation.isPending || updateMutation.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this booking link?</DialogTitle>
          <DialogDescription>
            Its page stops working immediately. The address /book/{link.slug} stays
            reserved to this deleted link for good — nobody else can publish under
            it, and you can&apos;t reuse it either. If you might want it back later,
            deactivate it instead.
          </DialogDescription>
        </DialogHeader>

        {serverError && (
          <p role="alert" className="text-sm text-red-600">
            {serverError}
          </p>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button type="button" variant="outline" onClick={handleDeactivate} disabled={pending}>
            Deactivate instead
          </Button>
          <Button type="button" variant="destructive" onClick={handleDelete} disabled={pending}>
            Delete link
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
