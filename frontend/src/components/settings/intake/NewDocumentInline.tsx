// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateIntakeDocument,
  usePublishIntakeDocument,
} from "@/hooks/useIntakeDocuments"
import {
  DOCUMENT_BODY_LABEL,
  NEW_DOCUMENT_NAME_LABEL,
  PUBLISH_AND_USE,
} from "./intakeCopy"

interface NewDocumentInlineProps {
  /** Prefix for the field ids, so two open items do not collide. */
  idPrefix: string
  /** Called with the new document's key and title once it is published. */
  onCreated: (documentKey: string, title: string) => void
}

/**
 * Write a document from the question that asks for it, without leaving the
 * packet. It is created and published through the same calls the Documents
 * card makes, so it lands there as an ordinary document with a version, and
 * is edited there afterwards.
 *
 * Nothing is greyed out over a missing name or text: Publish says which is
 * missing beside the field.
 */
export function NewDocumentInline({ idPrefix, onCreated }: NewDocumentInlineProps) {
  const create = useCreateIntakeDocument()
  const publish = usePublishIntakeDocument()
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [checked, setChecked] = useState(false)

  const nameId = `${idPrefix}-new-document-name`
  const bodyId = `${idPrefix}-new-document-body`
  const titleMissing = checked && !title.trim()
  const bodyMissing = checked && !body.trim()
  const busy = create.isPending || publish.isPending
  const error = create.error ?? publish.error

  async function submit() {
    if (!title.trim() || !body.trim()) {
      setChecked(true)
      document.getElementById(!title.trim() ? nameId : bodyId)?.focus()
      return
    }
    const created = await create.mutateAsync({ title: title.trim(), body_markdown: body })
    const published = await publish.mutateAsync(created.id)
    onCreated(published.document_key, published.title)
  }

  return (
    <div className="space-y-2 rounded-xl border border-border p-3">
      <div className="space-y-1">
        <Label htmlFor={nameId}>{NEW_DOCUMENT_NAME_LABEL}</Label>
        <Input
          id={nameId}
          value={title}
          maxLength={200}
          aria-invalid={titleMissing || undefined}
          onChange={(e) => setTitle(e.target.value)}
        />
        {titleMissing && <p className="text-[12px] text-red-700">Give the document a name.</p>}
      </div>
      <div className="space-y-1">
        <Label htmlFor={bodyId}>{DOCUMENT_BODY_LABEL}</Label>
        <Textarea
          id={bodyId}
          rows={6}
          value={body}
          aria-invalid={bodyMissing || undefined}
          onChange={(e) => setBody(e.target.value)}
        />
        {bodyMissing && <p className="text-[12px] text-red-700">Write what they read.</p>}
      </div>
      <Button type="button" size="sm" onClick={() => void submit().catch(() => undefined)} disabled={busy}>
        {PUBLISH_AND_USE}
      </Button>
      {error && (
        <p role="alert" className="text-[12.5px] text-destructive">
          {error instanceof Error && error.message ? error.message : "That could not be published."}
        </p>
      )}
    </div>
  )
}
