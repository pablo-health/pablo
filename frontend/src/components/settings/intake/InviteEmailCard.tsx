// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef, useState } from "react"

import { SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  useInviteTemplate,
  useInviteTemplatePreview,
  useResetInviteTemplate,
  useSaveInviteTemplate,
} from "@/hooks/useInviteTemplate"
import type { InviteTemplate, InviteTemplateDraft } from "@/lib/api/inviteTemplate"

const PREVIEW_DELAY_MS = 400

/**
 * The practice's portal invitation email: subject, plain-text message, and
 * the placeholders filled in when it is sent.
 *
 * Renders nothing where the deployment's email sends fixed wording — an
 * editor whose text is never used would be a setting that does nothing.
 *
 * The preview beside it is rendered by the server for an example client, by
 * the same code that renders the real email.
 */
export function InviteEmailCard() {
  const { data: template } = useInviteTemplate()
  if (!template?.editable) return null
  return <InviteEmailEditor template={template} />
}

function InviteEmailEditor({ template }: { template: InviteTemplate }) {
  const save = useSaveInviteTemplate()
  const resetToDefault = useResetInviteTemplate()
  const bodyRef = useRef<HTMLTextAreaElement>(null)

  const [draft, setDraft] = useState<InviteTemplateDraft>({
    subject: template.subject,
    body: template.body,
  })
  const [settled, setSettled] = useState<InviteTemplateDraft>(draft)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(draft), PREVIEW_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [draft])

  const { data: preview } = useInviteTemplatePreview(settled)
  const problems = preview?.problems ?? []
  const unchanged = draft.subject === template.subject && draft.body === template.body

  function edit(next: Partial<InviteTemplateDraft>) {
    setSaved(false)
    setDraft((current) => ({ ...current, ...next }))
  }

  function insert(name: string) {
    const token = `{{${name}}}`
    const area = bodyRef.current
    const start = area?.selectionStart ?? draft.body.length
    const end = area?.selectionEnd ?? draft.body.length
    edit({ body: draft.body.slice(0, start) + token + draft.body.slice(end) })
    requestAnimationFrame(() => {
      area?.focus()
      area?.setSelectionRange(start + token.length, start + token.length)
    })
  }

  async function onSave() {
    await save.mutateAsync(draft)
    setSaved(true)
  }

  return (
    <SettingsCard
      title="Invitation email"
      description="What a client receives when you invite them to the portal. The text message carries only their sign-in code."
    >
      <div className="grid gap-6 lg:grid-cols-2" data-testid="invite-email-card">
        <div className="space-y-4">
          <div className="form-group">
            <Label htmlFor="invite-subject">Subject</Label>
            <Input
              id="invite-subject"
              value={draft.subject}
              onChange={(event) => edit({ subject: event.target.value })}
            />
          </div>
          <div className="form-group">
            <Label htmlFor="invite-body">Message</Label>
            <Textarea
              id="invite-body"
              ref={bodyRef}
              rows={9}
              value={draft.body}
              onChange={(event) => edit({ body: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-neutral-500">Insert</p>
            <div className="flex flex-wrap gap-2">
              {template.placeholders.map((placeholder) => (
                <Button
                  key={placeholder.name}
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => insert(placeholder.name)}
                >
                  {placeholder.label}
                </Button>
              ))}
            </div>
          </div>

          {problems.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-red-700" data-testid="invite-email-problems">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          {save.isError && (
            <p className="text-sm text-red-700">The email could not be saved. Try again.</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              onClick={onSave}
              disabled={unchanged || problems.length > 0 || save.isPending}
            >
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            {!template.is_default && (
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  resetToDefault.mutate(undefined, {
                    onSuccess: (standard) => {
                      setSaved(false)
                      setDraft({ subject: standard.subject, body: standard.body })
                    },
                  })
                }
                disabled={resetToDefault.isPending}
              >
                Use the standard wording
              </Button>
            )}
            {saved && unchanged && <span className="text-sm text-neutral-600">Saved</span>}
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium text-neutral-500">Preview for an example client</p>
          <div
            className="space-y-2 rounded-lg border border-border bg-neutral-50 p-3 text-sm"
            data-testid="invite-email-card-preview"
          >
            <p className="font-medium">{preview?.subject}</p>
            <pre className="whitespace-pre-wrap border-t border-border pt-2 font-sans text-neutral-800">
              {preview?.text}
            </pre>
          </div>
        </div>
      </div>
    </SettingsCard>
  )
}
