// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NoteViewer Component
 *
 * Polymorphic clinical note viewer/editor. Branches on the session's
 * `note_type` to render the right layout — SOAP keeps the historical
 * four-section document; Narrative renders a single free-form body.
 *
 * Replaces SOAPViewer; SOAP rendering is preserved pixel-for-pixel.
 */

"use client"

import { useState } from "react"
import { Download, Edit, X, Save } from "lucide-react"
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
import { exportSOAPToPDF } from "@/lib/utils/pdfExport"
import type {
  NarrativeNoteContent,
  NoteContent,
  SOAPNoteContent,
  SOAPNoteModel,
  SOAPSentence,
  SessionStatus,
  SessionResponse,
  ClinicalObservation,
  StructuredSOAPNoteModel,
} from "@/types/sessions"
import {
  SubFieldEditor,
  SECTION_SUBFIELDS,
  narrativeToStructured,
  structuredToNarrative,
  type StructuredEditState,
} from "./SubFieldEditor"
import {
  ClinicalObservationForm,
  EMPTY_CLINICAL_OBSERVATION,
  formatClinicalObservation,
} from "./ClinicalObservationForm"
import { parseNarrativeBlocks } from "@/lib/utils/narrativeParser"
import { SourceBadge, SourceHighlight } from "./SourceBadge"

export interface NoteViewerProps {
  note: NoteContent | null
  noteEdited: NoteContent | null
  sessionId: string
  session: SessionResponse
  status: SessionStatus
  readonly?: boolean
  onSave?: (editedNote: NoteContent) => void
  onClaimClick?: (sourceSegmentIds: number[]) => void
  className?: string
}

export function NoteViewer(props: NoteViewerProps) {
  const noteType = props.session.note_type
  const display = props.noteEdited ?? props.note

  if (display && display.note_type !== noteType) {
    // Shouldn't happen, but never render the wrong editor against the wrong shape.
    return null
  }

  if (noteType === "narrative") {
    return (
      <NarrativeNoteView
        {...props}
        note={asNarrative(props.note)}
        noteEdited={asNarrative(props.noteEdited)}
      />
    )
  }

  return (
    <SOAPNoteView
      {...props}
      note={asSOAP(props.note)}
      noteEdited={asSOAP(props.noteEdited)}
    />
  )
}

function asSOAP(n: NoteContent | null): SOAPNoteContent | null {
  return n && n.note_type === "soap" ? n : null
}

function asNarrative(n: NoteContent | null): NarrativeNoteContent | null {
  return n && n.note_type === "narrative" ? n : null
}

// --- SOAP --------------------------------------------------------------

const SOAP_SECTIONS = [
  { key: "subjective" as const, label: "Subjective" },
  { key: "objective" as const, label: "Objective" },
  { key: "assessment" as const, label: "Assessment" },
  { key: "plan" as const, label: "Plan" },
] as const

interface SOAPViewProps extends Omit<NoteViewerProps, "note" | "noteEdited"> {
  note: SOAPNoteContent | null
  noteEdited: SOAPNoteContent | null
}

function SOAPNoteView({
  note,
  noteEdited,
  session,
  status,
  readonly = false,
  onSave,
  onClaimClick,
  className,
}: SOAPViewProps) {
  const [editMode, setEditMode] = useState(false)
  const [editState, setEditState] = useState<StructuredEditState | null>(null)
  const [initialEditState, setInitialEditState] = useState<string>("")
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)
  const [clinicalObs, setClinicalObs] = useState<ClinicalObservation>(EMPTY_CLINICAL_OBSERVATION)

  const displayNote: SOAPNoteModel | null = noteEdited ?? note
  const isEdited = !!noteEdited
  const canEdit = status === "pending_review" && !readonly
  const structured = session.soap_note_structured

  const hasUnsavedChanges = () => {
    if (!editState) return false
    return JSON.stringify(editState) !== initialEditState
  }

  const enterEditMode = () => {
    if (!displayNote) return
    const structured = narrativeToStructured(displayNote)
    setEditState(structured)
    setInitialEditState(JSON.stringify(structured))
    setEditMode(true)
  }

  const handleSave = () => {
    if (editState) {
      const narrative = structuredToNarrative(editState)
      const obsText = formatClinicalObservation(clinicalObs)
      if (obsText !== "**Clinician Observations:**") {
        narrative.objective = narrative.objective
          ? `${narrative.objective}\n\n${obsText}`
          : obsText
      }
      onSave?.({ note_type: "soap", ...narrative })
      setEditMode(false)
      setEditState(null)
    }
  }

  const handleCancel = () => {
    if (hasUnsavedChanges()) {
      setShowConfirmDialog(true)
    } else {
      setEditMode(false)
      setEditState(null)
    }
  }

  const handleDiscardChanges = () => {
    setEditMode(false)
    setEditState(null)
    setShowConfirmDialog(false)
  }

  const handlePDFExport = () => {
    if (displayNote) {
      const { subjective, objective, assessment, plan } = displayNote
      exportSOAPToPDF(session, { subjective, objective, assessment, plan })
    }
  }

  if (!displayNote) {
    return (
      <div className={cn("card text-center py-12", className)}>
        <p className="text-neutral-600">SOAP note not yet generated</p>
      </div>
    )
  }

  return (
    <div className={cn("card space-y-4", className)}>
      {/* Header */}
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-semibold text-neutral-900">SOAP Note</h3>
          {isEdited ? (
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
          <Button variant="outline" size="sm" onClick={handlePDFExport}>
            <Download className="w-4 h-4 mr-2" />
            Export PDF
          </Button>

          {canEdit && !editMode && (
            <Button size="sm" onClick={enterEditMode}>
              <Edit className="w-4 h-4 mr-2" />
              Edit
            </Button>
          )}
        </div>
      </div>

      {/* Document View — all sections rendered vertically */}
      <div className="divide-y divide-neutral-200">
        {SOAP_SECTIONS.map((section) => (
          <div key={section.key} className="py-5 first:pt-2">
            <h4 className="text-base font-semibold text-neutral-900 mb-3">
              {section.label}
            </h4>

            {section.key === "objective" ? (
              <>
                <div className="rounded-lg border border-blue-200 bg-blue-50/50 p-4">
                  <p className="text-xs font-medium text-blue-600 mb-3 uppercase tracking-wide">
                    Based on transcript
                  </p>
                  {editMode && editState ? (
                    <SubFieldEditor
                      sectionKey={section.key}
                      data={editState[section.key]}
                      onChange={(updated) =>
                        setEditState((prev) => prev ? { ...prev, [section.key]: updated } as StructuredEditState : null)
                      }
                    />
                  ) : structured ? (
                    <StructuredContent
                      sectionKey={section.key}
                      structured={structured}
                      onClaimClick={onClaimClick}
                    />
                  ) : (
                    <NarrativeContent text={displayNote[section.key]} />
                  )}
                </div>
                {canEdit && (
                  <ClinicalObservationForm
                    value={clinicalObs}
                    onChange={setClinicalObs}
                    readonly={readonly}
                    className="mt-4"
                  />
                )}
              </>
            ) : (
              <>
                {editMode && editState ? (
                  <SubFieldEditor
                    sectionKey={section.key}
                    data={editState[section.key]}
                    onChange={(updated) =>
                      setEditState((prev) => prev && { ...prev, [section.key]: updated })
                    }
                  />
                ) : structured ? (
                  <StructuredContent
                    sectionKey={section.key}
                    structured={structured}
                    onClaimClick={onClaimClick}
                  />
                ) : (
                  <NarrativeContent text={displayNote[section.key]} />
                )}
              </>
            )}
          </div>
        ))}
      </div>

      {/* Edit Mode Actions */}
      {editMode && (
        <div className="flex justify-end gap-2 pt-4 border-t border-neutral-200">
          <Button variant="outline" onClick={handleCancel}>
            <X className="w-4 h-4 mr-2" />
            Cancel
          </Button>
          <Button onClick={handleSave}>
            <Save className="w-4 h-4 mr-2" />
            Save Changes
          </Button>
        </div>
      )}

      {/* Unsaved Changes Dialog */}
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

/**
 * Renders structured SOAP sub-fields with source verification indicators.
 * Uses the structured data model to show verified/unverified badges per claim.
 */
function StructuredContent({
  sectionKey,
  structured,
  onClaimClick,
}: {
  sectionKey: string
  structured: StructuredSOAPNoteModel
  onClaimClick?: (sourceSegmentIds: number[]) => void
}) {
  const fields = SECTION_SUBFIELDS[sectionKey]
  if (!fields) return null

  const sectionData = structured[sectionKey as keyof StructuredSOAPNoteModel]
  if (!sectionData || typeof sectionData === "string") return null

  const record = sectionData as unknown as Record<string, SOAPSentence | SOAPSentence[] | null>

  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const value = record[field.key]
        if (!value) return null

        if (field.type === "list") {
          const items = value as SOAPSentence[]
          if (items.length === 0) return null
          return (
            <div key={field.key}>
              <h5 className="text-sm font-medium text-neutral-600 mb-1">
                {field.label}
              </h5>
              <ul className="space-y-1">
                {items.map((item, i) => {
                  const isClickable = item.source_segment_ids.length > 0 && !!onClaimClick
                  return (
                    <SourceHighlight
                      key={i}
                      sourceSegmentIds={item.source_segment_ids}
                      confidenceLevel={item.confidence_level}
                    >
                      <li
                        className={cn(
                          "text-sm text-neutral-900 flex items-start gap-1",
                          isClickable && "cursor-pointer hover:bg-blue-50 rounded px-1 -mx-1 transition-colors",
                        )}
                        role={isClickable ? "button" : undefined}
                        tabIndex={isClickable ? 0 : undefined}
                        onClick={isClickable ? () => onClaimClick(item.source_segment_ids) : undefined}
                        onKeyDown={isClickable ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault()
                            onClaimClick(item.source_segment_ids)
                          }
                        } : undefined}
                      >
                        <span className="shrink-0">-</span>
                        <span>{item.text}</span>
                        <SourceBadge
                          sourceSegmentIds={item.source_segment_ids}
                          confidenceLevel={item.confidence_level}
                          confidenceScore={item.confidence_score}
                        />
                      </li>
                    </SourceHighlight>
                  )
                })}
              </ul>
            </div>
          )
        }

        const sentence = value as SOAPSentence
        if (!sentence.text.trim()) return null
        const isClickable = sentence.source_segment_ids.length > 0 && !!onClaimClick

        return (
          <SourceHighlight
            key={field.key}
            sourceSegmentIds={sentence.source_segment_ids}
            confidenceLevel={sentence.confidence_level}
            className={cn(
              isClickable && "cursor-pointer hover:bg-blue-50 rounded transition-colors",
            )}
            onClick={isClickable ? () => onClaimClick(sentence.source_segment_ids) : undefined}
            role={isClickable ? "button" : undefined}
            tabIndex={isClickable ? 0 : undefined}
            onKeyDown={isClickable ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onClaimClick(sentence.source_segment_ids)
              }
            } : undefined}
          >
            <h5 className="text-sm font-medium text-neutral-600 mb-1">
              {field.label}
              <SourceBadge
                sourceSegmentIds={sentence.source_segment_ids}
                confidenceLevel={sentence.confidence_level}
                confidenceScore={sentence.confidence_score}
              />
            </h5>
            <p className="text-sm text-neutral-900 whitespace-pre-wrap leading-relaxed">
              {sentence.text}
            </p>
          </SourceHighlight>
        )
      })}
    </div>
  )
}

/**
 * Renders narrative text with sub-field labels as proper headings.
 * Parses **Label:** markdown patterns into structured display.
 * Used as fallback when structured data is not available.
 */
function NarrativeContent({ text }: { text: string }) {
  const blocks = parseNarrativeBlocks(text)

  if (blocks.length === 0) {
    return (
      <p className="text-sm text-neutral-500 italic">No content</p>
    )
  }

  return (
    <div className="space-y-3">
      {blocks.map((block, i) => (
        <div key={i}>
          {block.label && (
            <h5 className="text-sm font-medium text-neutral-600 mb-1">
              {block.label}
            </h5>
          )}
          <p className="text-sm text-neutral-900 whitespace-pre-wrap leading-relaxed">
            {block.content}
          </p>
        </div>
      ))}
    </div>
  )
}

// --- Narrative ---------------------------------------------------------

interface NarrativeViewProps extends Omit<NoteViewerProps, "note" | "noteEdited"> {
  note: NarrativeNoteContent | null
  noteEdited: NarrativeNoteContent | null
}

function NarrativeNoteView({
  note,
  noteEdited,
  status,
  readonly = false,
  onSave,
  className,
}: NarrativeViewProps) {
  const [editMode, setEditMode] = useState(false)
  const [draft, setDraft] = useState<string>("")
  const [initialDraft, setInitialDraft] = useState<string>("")
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)

  const displayNote = noteEdited ?? note
  const isEdited = !!noteEdited
  const canEdit = status === "pending_review" && !readonly

  const hasUnsavedChanges = () => draft !== initialDraft

  const enterEditMode = () => {
    if (!displayNote) return
    setDraft(displayNote.body)
    setInitialDraft(displayNote.body)
    setEditMode(true)
  }

  const handleSave = () => {
    onSave?.({ note_type: "narrative", body: draft })
    setEditMode(false)
  }

  const handleCancel = () => {
    if (hasUnsavedChanges()) {
      setShowConfirmDialog(true)
    } else {
      setEditMode(false)
    }
  }

  const handleDiscardChanges = () => {
    setEditMode(false)
    setShowConfirmDialog(false)
  }

  if (!displayNote) {
    return (
      <div className={cn("card text-center py-12", className)}>
        <p className="text-neutral-600">Narrative note not yet generated</p>
      </div>
    )
  }

  return (
    <div className={cn("card space-y-4", className)}>
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-2">
          <h3 className="text-lg font-semibold text-neutral-900">Narrative Note</h3>
          {isEdited ? (
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

      <div className="py-2">
        {editMode ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full min-h-[320px] p-3 border border-neutral-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 text-sm"
            placeholder="Enter narrative note..."
            aria-label="Narrative note body"
          />
        ) : displayNote.body.trim() ? (
          <p className="text-sm text-neutral-900 whitespace-pre-wrap leading-relaxed">
            {displayNote.body}
          </p>
        ) : (
          <p className="text-sm text-neutral-500 italic">No content</p>
        )}
      </div>

      {editMode && (
        <div className="flex justify-end gap-2 pt-4 border-t border-neutral-200">
          <Button variant="outline" onClick={handleCancel}>
            <X className="w-4 h-4 mr-2" />
            Cancel
          </Button>
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
