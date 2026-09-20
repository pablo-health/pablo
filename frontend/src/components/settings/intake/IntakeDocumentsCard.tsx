// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { SettingsBadge, SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateIntakeDocument,
  useIntakeDocuments,
  useNewIntakeDocumentVersion,
  usePublishIntakeDocument,
  useSaveIntakeDocument,
} from "@/hooks/useIntakeDocuments"
import type { IntakeDocument } from "@/types/intakeDocuments"
import {
  ADD_DOCUMENT,
  DOCUMENTS_DESCRIPTION,
  DOCUMENTS_EMPTY,
  DOCUMENTS_TITLE,
  DOCUMENT_BODY_LABEL,
  DOCUMENT_NAME_LABEL,
  DOCUMENT_PREVIEW_LABEL,
  DOCUMENT_PUBLISHED_NOTICE,
  DRAFT_BADGE,
  NEW_DOCUMENT_NAME,
  NEW_DOCUMENT_VERSION_BUTTON,
  PUBLISHED_BADGE,
  PUBLISH_BUTTON,
} from "./intakeCopy"

/** What the server said, or a plain fallback if it said nothing readable. */
function messageOf(error: unknown): string | null {
  if (!error) return null
  if (error instanceof Error && error.message) return error.message
  return "That could not be saved."
}

interface DocumentEditorProps {
  document: IntakeDocument
  onSave: (input: { title: string; body_markdown: string }) => void
  onPublish: () => void
  saving?: boolean
  publishing?: boolean
}

/**
 * One draft's wording, edited and previewed side by side.
 *
 * The preview is the server's own rendering of the version as it was last
 * saved, not a second renderer run over the text in the box. That is
 * deliberate: what a practice proofreads has to be what a patient is shown,
 * and two renderers are two chances to differ. The consequence is that the
 * preview catches up on save, which is why the editor says so.
 */
function DocumentEditor({
  document,
  onSave,
  onPublish,
  saving,
  publishing,
}: DocumentEditorProps) {
  const [title, setTitle] = useState(document.title)
  const [body, setBody] = useState(document.body_markdown)
  const [shownId, setShownId] = useState(document.id)

  // A different version is different wording, so switching between two of
  // them starts over rather than leaving the previous one's text on screen
  // under the new one's heading. Adjusted during render rather than in an
  // effect, which is React's own answer for state derived from a prop.
  if (shownId !== document.id) {
    setShownId(document.id)
    setTitle(document.title)
    setBody(document.body_markdown)
  }

  return (
    <div className="space-y-3">
      <div>
        <Label htmlFor={`${document.id}-title`}>{DOCUMENT_NAME_LABEL}</Label>
        <Input
          id={`${document.id}-title`}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor={`${document.id}-body`}>{DOCUMENT_BODY_LABEL}</Label>
          <Textarea
            id={`${document.id}-body`}
            rows={14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </div>
        <div>
          {/* A label rather than a <label>: the preview is a region to read,
              not a control to fill in, so nothing here takes focus. */}
          <span className="text-sm font-medium">{DOCUMENT_PREVIEW_LABEL}</span>
          <div
            role="region"
            aria-label={DOCUMENT_PREVIEW_LABEL}
            className="prose-sm h-full max-h-[22rem] overflow-y-auto rounded-xl border border-border p-3 text-sm text-foreground"
            // Rendered by the server from this document's markdown: escaped
            // first and marked up second, so nothing a practice typed can be
            // markup. See backend/app/intake/documents.py.
            dangerouslySetInnerHTML={{ __html: document.rendered_html }}
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          onClick={() => onSave({ title, body_markdown: body })}
          disabled={saving}
        >
          Save
        </Button>
        <Button type="button" variant="outline" onClick={onPublish} disabled={publishing}>
          {PUBLISH_BUTTON}
        </Button>
      </div>
    </div>
  )
}

/**
 * Practice > Patient portal > Documents.
 *
 * The documents a practice asks people to sign, beside the forms it asks
 * them to fill in. One is open at a time, for the same reason the form
 * builder opens one form: a page of every version of every document is a
 * page nobody reads.
 *
 * The list shows one entry per document — its newest version — because a
 * practice thinks in documents. Older versions stay readable through the
 * records that point at them rather than through this screen.
 */
export function IntakeDocumentsCard() {
  const { data: documents } = useIntakeDocuments()
  const createDocument = useCreateIntakeDocument()
  const saveDocument = useSaveIntakeDocument()
  const publish = usePublishIntakeDocument()
  const newVersion = useNewIntakeDocumentVersion()

  const [openId, setOpenId] = useState<string | null>(null)

  const list = documents ?? []
  const error = messageOf(publish.error) ?? messageOf(saveDocument.error)

  return (
    <SettingsCard title={DOCUMENTS_TITLE} description={DOCUMENTS_DESCRIPTION}>
      {list.length === 0 && (
        <p className="text-[13px] text-muted-foreground">{DOCUMENTS_EMPTY}</p>
      )}

      <ul className="space-y-2">
        {list.map((document) => {
          const open = openId === document.id
          const published = document.published_at !== null
          return (
            <li key={document.id} className="rounded-xl border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setOpenId(open ? null : document.id)}
                  aria-expanded={open}
                >
                  <span className="text-sm font-semibold text-foreground">{document.title}</span>
                  <span className="ml-2 text-[12.5px] text-muted-foreground">
                    {`Version ${document.version}`}
                  </span>
                </button>
                <SettingsBadge>{published ? PUBLISHED_BADGE : DRAFT_BADGE}</SettingsBadge>
              </div>

              {open && (
                <div className="mt-3 space-y-3 border-t border-border pt-3">
                  {published ? (
                    <>
                      <p className="text-[13px] text-muted-foreground">
                        {DOCUMENT_PUBLISHED_NOTICE}
                      </p>
                      <div
                        className="prose-sm max-h-[22rem] overflow-y-auto rounded-xl border border-border p-3 text-sm text-foreground"
                        // Server-rendered from this version's markdown; see
                        // the note in DocumentEditor.
                        dangerouslySetInnerHTML={{ __html: document.rendered_html }}
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          newVersion.mutate(document.id, {
                            onSuccess: (draft) => setOpenId(draft.id),
                          })
                        }
                        disabled={newVersion.isPending}
                      >
                        {NEW_DOCUMENT_VERSION_BUTTON}
                      </Button>
                    </>
                  ) : (
                    <DocumentEditor
                      document={document}
                      onSave={(input) => saveDocument.mutate({ id: document.id, input })}
                      onPublish={() => publish.mutate(document.id)}
                      saving={saveDocument.isPending}
                      publishing={publish.isPending}
                    />
                  )}
                  {error && (
                    <p role="alert" className="text-[13px] text-destructive">
                      {error}
                    </p>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>

      <div className="mt-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            createDocument.mutate(
              { title: NEW_DOCUMENT_NAME, body_markdown: "" },
              { onSuccess: (created) => setOpenId(created.id) }
            )
          }
          disabled={createDocument.isPending}
        >
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          {ADD_DOCUMENT}
        </Button>
      </div>
    </SettingsCard>
  )
}
