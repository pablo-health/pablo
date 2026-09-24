// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SchemaNoteView Component
 *
 * Viewer/editor for every note type that is not SOAP or Narrative (DAP,
 * BIRP, GIRP, Meeting Summary, Intake, a practice's own types, ...). The
 * layout comes from the note type's catalog definition — fetched at the
 * version the note was written against, so a practice editing its type later
 * does not reshape notes already written with it.
 *
 * Content is ``{section_key: {field_key: value}}``. Each field gets one box:
 * `text` is a paragraph, `list` a bulleted list edited one item per line, and
 * `structured` is shown read-only (its shape is type-specific and has no
 * generic editor yet). Saving keeps every value the definition does not
 * describe, so an edit never drops data the layout can't show.
 */

"use client"

import { useState } from "react"
import { Edit, Save, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useNoteType } from "@/hooks/useNoteTypes"
import type { NoteFieldSchema, NoteTypeSchema } from "@/types/noteTypes"
import type {
  NoteContent,
  SchemaNoteContent,
  SchemaSectionValues,
} from "@/types/sessions"

export interface SchemaNoteViewProps {
  noteTypeKey: string
  /** Version of a practice-defined type the note records; null for built-in types. */
  version: number | null
  note: SchemaNoteContent | null
  noteEdited: SchemaNoteContent | null
  readonly?: boolean
  onSave?: (editedNote: NoteContent) => void
  className?: string
}

export function SchemaNoteView({
  noteTypeKey,
  version,
  className,
  ...rest
}: SchemaNoteViewProps) {
  const { data: definition, isLoading } = useNoteType(noteTypeKey, version)

  if (isLoading) {
    return (
      <div className={cn("card text-center py-12", className)}>
        <p className="text-neutral-600">Loading note…</p>
      </div>
    )
  }

  if (!definition) {
    return (
      <div className={cn("card text-center py-12", className)}>
        <p className="text-neutral-600">This note&apos;s layout couldn&apos;t be loaded.</p>
      </div>
    )
  }

  return (
    <SchemaNoteBody
      // A different definition means a different set of boxes; start fresh.
      key={`${definition.key}@${definition.version ?? "builtin"}`}
      definition={definition}
      className={className}
      {...rest}
    />
  )
}

type Drafts = Record<string, string>

const draftKey = (sectionKey: string, fieldKey: string) => `${sectionKey}.${fieldKey}`

/** A stored value as display lines for a `list` field. */
function listItems(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v)).filter((v) => v.trim())
  if (typeof value === "string") return value.split("\n").filter((v) => v.trim())
  return []
}

/** A stored value as display text for a `text` field. */
function textValue(value: unknown): string {
  if (typeof value === "string") return value
  if (Array.isArray(value)) return value.map((v) => String(v)).join("\n")
  if (value == null) return ""
  return JSON.stringify(value, null, 2)
}

function draftsFrom(
  definition: NoteTypeSchema,
  sections: Record<string, SchemaSectionValues>,
): Drafts {
  const drafts: Drafts = {}
  for (const section of definition.sections) {
    for (const field of section.fields) {
      if (field.kind === "structured") continue
      const value = sections[section.key]?.[field.key]
      drafts[draftKey(section.key, field.key)] =
        field.kind === "list" ? listItems(value).join("\n") : textValue(value)
    }
  }
  return drafts
}

function sectionsFrom(
  definition: NoteTypeSchema,
  base: Record<string, SchemaSectionValues>,
  drafts: Drafts,
): Record<string, SchemaSectionValues> {
  const out: Record<string, SchemaSectionValues> = {}
  for (const [key, values] of Object.entries(base)) out[key] = { ...values }
  for (const section of definition.sections) {
    const values: SchemaSectionValues = { ...(out[section.key] ?? {}) }
    for (const field of section.fields) {
      if (field.kind === "structured") continue
      const draft = drafts[draftKey(section.key, field.key)] ?? ""
      values[field.key] =
        field.kind === "list"
          ? draft.split("\n").map((line) => line.trim()).filter(Boolean)
          : draft
    }
    out[section.key] = values
  }
  return out
}

function isEmptyValue(value: unknown): boolean {
  if (value == null) return true
  if (typeof value === "string") return !value.trim()
  if (Array.isArray(value)) return listItems(value).length === 0
  if (typeof value === "object") return Object.keys(value).length === 0
  return false
}

interface SchemaNoteBodyProps extends Omit<SchemaNoteViewProps, "noteTypeKey" | "version"> {
  definition: NoteTypeSchema
}

function SchemaNoteBody({
  definition,
  note,
  noteEdited,
  readonly = false,
  onSave,
  className,
}: SchemaNoteBodyProps) {
  // Same blank-note behaviour as the SOAP and Narrative views: a manually
  // created, empty note opens straight in the editor.
  const isBlank = !noteEdited && !note
  const canEdit = !readonly && !!onSave
  const startEmptyEditing = isBlank && canEdit
  const isManual = isBlank

  const displaySections = (noteEdited ?? note)?.sections ?? {}
  const hasDisplay = !!(noteEdited ?? note) || startEmptyEditing
  const isEdited = !!noteEdited

  const [editMode, setEditMode] = useState(startEmptyEditing)
  const [drafts, setDrafts] = useState<Drafts>(() =>
    startEmptyEditing ? draftsFrom(definition, {}) : {},
  )
  const [initialDrafts, setInitialDrafts] = useState<string>(() =>
    startEmptyEditing ? JSON.stringify(draftsFrom(definition, {})) : "",
  )
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)

  const enterEditMode = () => {
    const next = draftsFrom(definition, displaySections)
    setDrafts(next)
    setInitialDrafts(JSON.stringify(next))
    setEditMode(true)
  }

  const handleSave = () => {
    onSave?.({
      note_type: "schema",
      key: definition.key,
      sections: sectionsFrom(definition, displaySections, drafts),
    })
    setEditMode(false)
  }

  const handleCancel = () => {
    if (JSON.stringify(drafts) !== initialDrafts) {
      setShowConfirmDialog(true)
    } else {
      setEditMode(false)
    }
  }

  const handleDiscardChanges = () => {
    setEditMode(false)
    setShowConfirmDialog(false)
  }

  if (!hasDisplay) {
    return (
      <div className={cn("card text-center py-12", className)}>
        <p className="text-neutral-600">{definition.label} note not yet generated</p>
      </div>
    )
  }

  return (
    <div className={cn("card space-y-4", className)}>
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-semibold text-neutral-900">{definition.label}</h3>
          {isManual ? null : isEdited ? (
            <span className="px-2 py-1 bg-secondary-100 text-secondary-700 text-xs font-medium rounded">
              Edited
            </span>
          ) : (
            <span className="px-2 py-1 bg-primary-100 text-primary-700 text-xs font-medium rounded">
              AI Generated
            </span>
          )}
        </div>

        <div className="flex gap-2">
          {canEdit && !editMode && (
            <Button size="sm" onClick={enterEditMode}>
              <Edit className="w-4 h-4 mr-2" />
              Edit
            </Button>
          )}
        </div>
      </div>

      <div className="divide-y divide-neutral-200">
        {definition.sections.map((section) => {
          const values = displaySections[section.key] ?? {}
          const shown = editMode
            ? section.fields
            : section.fields.filter((f) => !isEmptyValue(values[f.key]))
          return (
            <div key={section.key} className="py-5 first:pt-2">
              <h4 className="text-base font-semibold text-neutral-900 mb-3">
                {section.label}
              </h4>
              {shown.length === 0 ? (
                <p className="text-sm text-neutral-500 italic">No content</p>
              ) : (
                <div className="space-y-3">
                  {shown.map((field) => (
                    <FieldBlock
                      key={field.key}
                      field={field}
                      value={values[field.key]}
                      editing={editMode}
                      draft={drafts[draftKey(section.key, field.key)] ?? ""}
                      onDraftChange={(next) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [draftKey(section.key, field.key)]: next,
                        }))
                      }
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {editMode && (
        <div className="flex justify-end gap-2 pt-4 border-t border-neutral-200">
          {!isManual && (
            <Button variant="outline" onClick={handleCancel}>
              <X className="w-4 h-4 mr-2" />
              Cancel
            </Button>
          )}
          <Button onClick={handleSave}>
            <Save className="w-4 h-4 mr-2" />
            Save Changes
          </Button>
        </div>
      )}

      <Dialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unsaved Changes</DialogTitle>
            <DialogDescription>
              You have unsaved changes. Are you sure you want to discard them?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowConfirmDialog(false)}>
              Keep Editing
            </Button>
            <Button variant="destructive" onClick={handleDiscardChanges}>
              Discard Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

const TEXTAREA_CLASS =
  "w-full min-h-[96px] p-3 border border-neutral-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 text-sm"

function FieldBlock({
  field,
  value,
  editing,
  draft,
  onDraftChange,
}: {
  field: NoteFieldSchema
  value: unknown
  editing: boolean
  draft: string
  onDraftChange: (next: string) => void
}) {
  const label = (
    <h5 className="text-sm font-medium text-neutral-600 mb-1">{field.label}</h5>
  )

  if (field.kind === "structured") {
    return (
      <div>
        {label}
        {isEmptyValue(value) ? (
          <p className="text-sm text-neutral-500 italic">No content</p>
        ) : (
          <pre className="text-xs text-neutral-900 whitespace-pre-wrap rounded-lg bg-neutral-50 p-3">
            {JSON.stringify(value, null, 2)}
          </pre>
        )}
      </div>
    )
  }

  if (editing) {
    return (
      <div>
        {label}
        <textarea
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          className={TEXTAREA_CLASS}
          aria-label={field.label}
          placeholder={field.kind === "list" ? "One item per line" : undefined}
        />
      </div>
    )
  }

  if (field.kind === "list") {
    return (
      <div>
        {label}
        <ul className="space-y-1">
          {listItems(value).map((item, i) => (
            <li key={i} className="text-sm text-neutral-900 flex items-start gap-1">
              <span className="shrink-0">-</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div>
      {label}
      <p className="text-sm text-neutral-900 whitespace-pre-wrap leading-relaxed">
        {textValue(value)}
      </p>
    </div>
  )
}
