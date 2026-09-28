// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef, useState } from "react"

import { SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  usePortalWelcome,
  useResetPortalWelcome,
  useSavePortalWelcome,
} from "@/hooks/usePortalWelcome"
import { ApiError } from "@/lib/api/client"
import type { PortalWelcome, PortalWelcomeDraft } from "@/lib/api/portalWelcome"

/** Mirrors `backend/app/portal/welcome.py`; the server checks them again. */
const MAX_HEADING_LENGTH = 120
const MAX_BODY_LENGTH = 1000

/** A run of braces around the name is one token, as on the server. */
const PRACTICE_NAME_RE = /\{+\s*practice_name\s*\}+/g

/**
 * The welcome a client reads on the portal's home screen: a heading and a
 * plain-text message, with the practice's name as the one placeholder.
 *
 * The preview is drawn here rather than by the server: filling in one name
 * needs no round trip. Text is rendered as text, never as markup, exactly as
 * the portal shows it.
 */
export function PortalWelcomeCard() {
  const { data: welcome } = usePortalWelcome()
  if (!welcome) return null
  return <PortalWelcomeEditor welcome={welcome} />
}

function fill(text: string, practiceName: string): string {
  return text.trim().replace(PRACTICE_NAME_RE, practiceName)
}

function saveProblems(error: unknown): string[] {
  if (!(error instanceof ApiError)) return []
  const problems = error.details?.problems
  return Array.isArray(problems) ? problems.map(String) : []
}

function PortalWelcomeEditor({ welcome }: { welcome: PortalWelcome }) {
  const save = useSavePortalWelcome()
  const resetToDefault = useResetPortalWelcome()
  const bodyRef = useRef<HTMLTextAreaElement>(null)

  const [draft, setDraft] = useState<PortalWelcomeDraft>({
    heading: welcome.heading,
    body: welcome.body,
  })
  const [saved, setSaved] = useState(false)

  const unchanged = draft.heading === welcome.heading && draft.body === welcome.body
  const empty = !draft.heading.trim() || !draft.body.trim()
  const problems = saveProblems(save.error)

  function edit(next: Partial<PortalWelcomeDraft>) {
    setSaved(false)
    save.reset()
    setDraft((current) => ({ ...current, ...next }))
  }

  function insert(name: string) {
    const token = `{${name}}`
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
      title="Welcome screen"
      description="The first thing a client reads when they open the portal."
    >
      <div className="grid gap-6 lg:grid-cols-2" data-testid="portal-welcome-card">
        <div className="space-y-4">
          <div className="form-group">
            <Label htmlFor="portal-welcome-heading">Heading</Label>
            <Input
              id="portal-welcome-heading"
              maxLength={MAX_HEADING_LENGTH}
              value={draft.heading}
              onChange={(event) => edit({ heading: event.target.value })}
            />
          </div>
          <div className="form-group">
            <Label htmlFor="portal-welcome-body">Message</Label>
            <Textarea
              id="portal-welcome-body"
              ref={bodyRef}
              rows={6}
              maxLength={MAX_BODY_LENGTH}
              value={draft.body}
              onChange={(event) => edit({ body: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-neutral-500">Insert</p>
            <div className="flex flex-wrap gap-2">
              {welcome.placeholders.map((placeholder) => (
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

          {problems.length > 0 ? (
            <ul className="list-disc pl-5 text-sm text-red-700" data-testid="portal-welcome-problems">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : (
            save.isError && (
              <p className="text-sm text-red-700">The welcome could not be saved. Try again.</p>
            )
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              onClick={() => onSave().catch(() => undefined)}
              disabled={unchanged || empty || save.isPending}
            >
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            {!welcome.is_default && (
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  resetToDefault.mutate(undefined, {
                    onSuccess: (standard) => {
                      setSaved(false)
                      save.reset()
                      setDraft({ heading: standard.heading, body: standard.body })
                    },
                  })
                }
                disabled={resetToDefault.isPending}
              >
                Use the default
              </Button>
            )}
            {saved && unchanged && <span className="text-sm text-neutral-600">Saved</span>}
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium text-neutral-500">Preview</p>
          <div
            className="space-y-2 rounded-lg border border-border bg-neutral-50 p-3 text-sm"
            data-testid="portal-welcome-card-preview"
          >
            <p className="font-medium">{fill(draft.heading, welcome.practice_name)}</p>
            <p className="whitespace-pre-wrap text-neutral-800">
              {fill(draft.body, welcome.practice_name)}
            </p>
          </div>
        </div>
      </div>
    </SettingsCard>
  )
}
