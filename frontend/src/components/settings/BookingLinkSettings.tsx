// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef, useState } from "react"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useBookingLinks,
  useCreateBookingLink,
  useUpdateBookingLink,
} from "@/hooks/useBookingLinks"
import { ApiError } from "@/lib/api/client"
import { SESSION_TYPES, SLUG_PATTERN } from "@/types/bookingLinks"
import type { BookingLink, CreateBookingLinkRequest } from "@/types/bookingLinks"

const SLUG_HELP = "3–64 characters: lowercase letters, numbers, dashes."
const SLUG_ERROR = "Slug must be 3–64 lowercase letters, numbers or dashes."

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return "Something went wrong. Please try again."
}

function bookingUrl(slug: string): string {
  return `${window.location.origin}/book/${slug}`
}

function LinkRow({ link }: { link: BookingLink }) {
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
      </div>
    </li>
  )
}

function CreateLinkForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: () => void }) {
  const createMutation = useCreateBookingLink()
  const [slug, setSlug] = useState("")
  const [hostName, setHostName] = useState("")
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [durationMinutes, setDurationMinutes] = useState("50")
  const [sessionType, setSessionType] = useState<string>(SESSION_TYPES[0])
  const [slugError, setSlugError] = useState<string | null>(null)
  const [serverError, setServerError] = useState<string | null>(null)

  function handleSlugChange(value: string) {
    setSlug(value.toLowerCase())
    setSlugError(null)
    setServerError(null)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setServerError(null)

    if (!hostName.trim() || !title.trim()) return

    if (!SLUG_PATTERN.test(slug)) {
      setSlugError(SLUG_ERROR)
      return
    }
    setSlugError(null)

    const data: CreateBookingLinkRequest = {
      slug,
      host_name: hostName.trim(),
      title: title.trim(),
      duration_minutes: Number(durationMinutes),
      session_type: sessionType,
    }
    if (description.trim()) data.description = description.trim()

    createMutation.mutate(data, {
      onSuccess: onCreated,
      onError: (err) => setServerError(errorMessage(err)),
    })
  }

  const slugMessage = slugError ?? serverError

  return (
    <form onSubmit={handleSubmit} className="space-y-4 rounded-md border border-neutral-200 p-4">
      <div className="grid gap-2">
        <Label htmlFor="link-slug">Slug</Label>
        <Input
          id="link-slug"
          value={slug}
          onChange={(e) => handleSlugChange(e.target.value)}
          placeholder="intro-call"
        />
        <p className="text-xs text-neutral-500">{SLUG_HELP}</p>
        {slug && <p className="text-xs text-neutral-500">/book/{slug}</p>}
        {slugMessage && (
          <p role="alert" className="text-sm text-red-600">
            {slugMessage}
          </p>
        )}
      </div>

      <div className="grid gap-2">
        <Label htmlFor="link-host-name">Host name</Label>
        <Input
          id="link-host-name"
          value={hostName}
          onChange={(e) => setHostName(e.target.value)}
          required
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="link-title">Title</Label>
        <Input
          id="link-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Intro call"
          required
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="link-description">Description</Label>
        <Textarea
          id="link-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-2">
          <Label htmlFor="link-duration">Length (minutes)</Label>
          <Input
            id="link-duration"
            type="number"
            min={5}
            max={480}
            value={durationMinutes}
            onChange={(e) => setDurationMinutes(e.target.value)}
            className="w-24"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="link-session-type">Session type</Label>
          <Select value={sessionType} onValueChange={setSessionType}>
            <SelectTrigger id="link-session-type" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SESSION_TYPES.map((type) => (
                <SelectItem key={type} value={type}>
                  {type}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={createMutation.isPending}>
          {createMutation.isPending ? "Creating..." : "Create link"}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

export function BookingLinkSettings() {
  const { data, isLoading, error } = useBookingLinks()
  const [formOpen, setFormOpen] = useState(false)

  const links = data?.data ?? []

  if (isLoading) {
    return (
      <div className="space-y-2" role="status">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  if (error) {
    return <p className="text-sm text-red-600">Couldn&apos;t load your booking links.</p>
  }

  return (
    <div className="space-y-4">
      {links.length === 0 && <p className="text-sm text-neutral-600">No booking links yet.</p>}

      {links.length > 0 && (
        <ul className="space-y-2">
          {links.map((link) => (
            <LinkRow key={link.id} link={link} />
          ))}
        </ul>
      )}

      {formOpen ? (
        <CreateLinkForm onCancel={() => setFormOpen(false)} onCreated={() => setFormOpen(false)} />
      ) : (
        <Button size="sm" onClick={() => setFormOpen(true)}>
          New booking link
        </Button>
      )}
    </div>
  )
}
