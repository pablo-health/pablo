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
 * `text` is a paragraph, `list` a bulleted list edited one item per line,
 * `diagnoses` a list of stated diagnoses edited one row each, and
 * `structured` is shown read-only (its shape is type-specific and has no
 * generic editor yet). Saving keeps every value the definition does not
 * describe, so an edit never drops data the layout can't show.
 */

"use client"

import { useState } from "react"
import { Download, Edit, Save, X } from "lucide-react"
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
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { schemaNotePdf } from "@/lib/notePdf"
import { inTheNote, isEmptyValue, listItems, textValue } from "@/lib/schemaNoteValues"
import { statedDiagnoses, type StatedDiagnosis } from "@/lib/statedDiagnoses"
import { areAllGrounded } from "@/lib/utils/grounding"
import { exportNoteToPDF, type PDFExportMetadata } from "@/lib/utils/pdfExport"
import type { NoteFieldSchema, NoteTypeSchema } from "@/types/noteTypes"
import type {
  NoteContent,
  SchemaNoteContent,
  SchemaSectionValues,
} from "@/types/sessions"
import { DiagnosesEditor, DiagnosesList, type DiagnosisAction } from "./DiagnosesField"
import { GroundingBadge } from "./GroundingBadge"

export interface SchemaNoteViewProps {
  noteTypeKey: string
  /** Version of a practice-defined type the note records; null for built-in types. */
  version: number | null
  note: SchemaNoteContent | null
  noteEdited: SchemaNoteContent | null
  readonly?: boolean
  onSave?: (editedNote: NoteContent) => void
  /** Offered beside each stated diagnosis while the note is not being edited. */
  diagnosisAction?: DiagnosisAction
  /** Header of the note's PDF. Export PDF is offered only when given. */
  pdfMetadata?: PDFExportMetadata
  /**
   * Original document text for an imported note. When set, each field shows
   * whether its text was found in the document, so one that wasn't is checked.
   */
  groundingSource?: string
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
        field.kind === "list"
          ? listItems(value).join("\n")
          : field.kind === "diagnoses"
            ? JSON.stringify(statedDiagnoses(value))
            : textValue(value)
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
          : field.kind === "diagnoses"
            ? keptDiagnoses(draft)
            : draft
    }
    out[section.key] = values
  }
  return out
}

/** A diagnoses draft as saved: parts trimmed, rows with no diagnosis dropped. */
function keptDiagnoses(draft: string): StatedDiagnosis[] {
  const parsed: unknown = draft ? JSON.parse(draft) : []
  return statedDiagnoses(parsed)
}

export interface SchemaNoteBodyProps extends Omit<SchemaNoteViewProps, "noteTypeKey" | "version"> {
  definition: NoteTypeSchema
}

export function SchemaNoteBody({
  definition,
  note,
  noteEdited,
  readonly = false,
  onSave,
  diagnosisAction,
  pdfMetadata,
  groundingSource,
  className,
}: SchemaNoteBodyProps) {
  const people = usePeopleTerm()
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

  // What the screen shows is what the PDF prints: the edit over the draft.
  const handlePDFExport = () => {
    if (pdfMetadata) exportNoteToPDF(pdfMetadata, schemaNotePdf(definition, displaySections), people)
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
          {pdfMetadata && !isBlank && (
            <Button variant="outline" size="sm" onClick={handlePDFExport}>
              <Download className="w-4 h-4 mr-2" />
              Export PDF
            </Button>
          )}
          {canEdit && !editMode && (
            <Button size="sm" onClick={enterEditMode}>
              <Edit className="w-4 h-4 mr-2" />
              Edit
            </Button>
          )}
        </div>
      </div>

      <div className="divide-y divide-neutral-200">
        {inTheNote(definition.sections).map((section) => {
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
                      diagnosisAction={diagnosisAction}
                      groundingSource={groundingSource}
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

/**
 * The words a field states, item by item, as the server's grounding check
 * reads them: a stated diagnosis is its label, code and status.
 */
function statedTexts(field: NoteFieldSchema, value: unknown): string[] {
  if (field.kind === "list") return listItems(value)
  if (field.kind === "diagnoses") {
    return statedDiagnoses(value).map((d) =>
      [d.label, d.code, d.status].filter(Boolean).join(" "),
    )
  }
  return [textValue(value)]
}

function FieldBlock({
  field,
  value,
  editing,
  diagnosisAction,
  groundingSource,
  draft,
  onDraftChange,
}: {
  field: NoteFieldSchema
  value: unknown
  editing: boolean
  diagnosisAction?: DiagnosisAction
  groundingSource?: string
  draft: string
  onDraftChange: (next: string) => void
}) {
  const showGrounding =
    groundingSource !== undefined &&
    !editing &&
    field.kind !== "structured" &&
    !isEmptyValue(value)
  const label = (
    <h5 className="text-sm font-medium text-neutral-600 mb-1">
      {field.label}
      {showGrounding && (
        <GroundingBadge grounded={areAllGrounded(statedTexts(field, value), groundingSource)} />
      )}
    </h5>
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

  if (field.kind === "diagnoses") {
    return (
      <div>
        {label}
        {editing ? (
          <DiagnosesEditor
            label={field.label}
            items={draft ? (JSON.parse(draft) as StatedDiagnosis[]) : []}
            onChange={(next) => onDraftChange(JSON.stringify(next))}
          />
        ) : (
          <DiagnosesList items={statedDiagnoses(value)} action={diagnosisAction} />
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
