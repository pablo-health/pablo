// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { useUpdateBookingLink } from "@/hooks/useBookingLinks"
import type { BookingLink } from "@/types/bookingLinks"
import { BookingLinkEditForm } from "./BookingLinkEditForm"

function bookingUrl(slug: string): string {
  return `${window.location.origin}/book/${slug}`
}

export function LinkRow({
  link,
  isEditing,
  onEdit,
  onCancelEdit,
  onSaved,
  onDeleteClick,
}: {
  link: BookingLink
  isEditing: boolean
  onEdit: () => void
  onCancelEdit: () => void
  onSaved: () => void
  onDeleteClick: () => void
}) {
  const [copied, setCopied] = useState(false)
  const urlRef = useRef<HTMLParagraphElement>(null)
  const updateMutation = useUpdateBookingLink()
  const url = bookingUrl(link.slug)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      const node = urlRef.current
      const selection = window.getSelection()
      if (node && selection) {
        const range = document.createRange()
        range.selectNodeContents(node)
        selection.removeAllRanges()
        selection.addRange(range)
      }
    }
  }

  function handleToggleActive() {
    updateMutation.mutate({ linkId: link.id, data: { is_active: !link.is_active } })
  }

  if (isEditing) {
    return (
      <li>
        <BookingLinkEditForm link={link} onCancel={onCancelEdit} onSaved={onSaved} />
      </li>
    )
  }

  return (
    <li className="space-y-2 rounded-md border border-neutral-200 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-neutral-900">{link.title}</p>
          <p className="text-sm text-neutral-900">/book/{link.slug}</p>
          <p ref={urlRef} className="text-xs text-neutral-500">
            {url}
          </p>
          <p className="text-xs text-neutral-500">
            {link.duration_minutes} min · {link.session_type}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-1 text-xs font-medium ${
            link.is_active ? "bg-secondary-100 text-secondary-700" : "bg-neutral-100 text-neutral-600"
          }`}
        >
          {link.is_active ? "Active" : "Inactive"}
        </span>
      </div>
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={handleCopy}>
          {copied ? "Copied" : "Copy link"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleToggleActive}
          disabled={updateMutation.isPending}
        >
          {link.is_active ? "Deactivate" : "Activate"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onEdit}>
          Edit
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onDeleteClick}>
          Delete
        </Button>
      </div>
    </li>
  )
}
