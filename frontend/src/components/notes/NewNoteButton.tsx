// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NewNoteButton
 *
 * Patient-scoped entry point for creating a clinical note. Offers two
 * on-ramps: uploading a session transcript (which generates a SOAP note via
 * a recording session), or creating a blank note of a ``context=session``
 * type (SOAP, Narrative, ...) to fill in by hand. Blank notes POST to
 * ``/api/patients/{pid}/notes`` with empty content and route to edit mode;
 * the transcript path hands off to ``UploadTranscriptDialog`` with the
 * patient pre-filled.
 *
 * A type that declares inputs (a psychiatric follow-up's place of service)
 * asks for them in the same dialog before the note is created; the server
 * refuses a note missing a required one. Types without inputs are created
 * on the click.
 *
 * Types reported as ``is_locked`` by the backend (e.g. Practice-tier
 * extension formats the caller hasn't subscribed to) render with a lock
 * affordance instead of an active button — clicking them surfaces an
 * upgrade hint rather than firing a request that the SaaS authorizer
 * would reject with 403.
 */

"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { FileText, FileUp, Lock, Plus, Sparkles, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { LabeledNoteInputs } from "@/components/notes/LabeledNoteInputs"
import { UploadTranscriptDialog } from "@/components/sessions/UploadTranscriptDialog"
import { ImportNotesDialog } from "@/components/sessions/ImportNotesDialog"
import { draftInputs, missingInputs } from "@/components/sessions/TranscriptNoteType"
import { useToast } from "@/components/ui/Toast"
import { ApiError } from "@/lib/api/client"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { filledInputs } from "@/lib/noteInputs"
import { useNoteTypes } from "@/hooks/useNoteTypes"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useCreateStandaloneNote } from "@/hooks/useNotes"
import { lockedNoteTypeMessage, type NoteTypeSchema } from "@/types/noteTypes"

export interface NewNoteButtonProps {
  patientId: string
}

export function NewNoteButton({ patientId }: NewNoteButtonProps) {
  const [open, setOpen] = useState(false)
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  // A picked type that asks for inputs, and what's been entered for them.
  const [picked, setPicked] = useState<NoteTypeSchema | null>(null)
  const [inputValues, setInputValues] = useState<Record<string, string>>({})
  const [showMissing, setShowMissing] = useState(false)
  const [inputsRejected, setInputsRejected] = useState(false)
  const router = useRouter()
  const { showToast } = useToast()
  const { data: catalog, isLoading } = useNoteTypes()
  const createNote = useCreateStandaloneNote()
  const { readOnly } = useReadOnlyMode()
  const people = usePeopleTerm()

  // Both on-ramps (transcript upload, blank note) and the import dialog hang
  // off this button, so hiding it closes the whole note-authoring flow.
  if (readOnly) return null

  const sessionTypes = (catalog?.note_types ?? []).filter(
    (t) => t.context === "session",
  )

  const pickedInputs = draftInputs(picked ?? undefined)
  const missing = new Set(
    showMissing ? missingInputs(pickedInputs, inputValues).map((i) => i.key) : [],
  )

  const choose = (type: NoteTypeSchema | null) => {
    setPicked(type)
    setInputValues({})
    setShowMissing(false)
    setInputsRejected(false)
  }

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) choose(null)
  }

  const create = async (type: NoteTypeSchema, inputs: Record<string, string> | null) => {
    try {
      const note = await createNote.mutateAsync({
        patientId,
        data: inputs ? { note_type: type.key, note_inputs: inputs } : { note_type: type.key },
      })
      handleOpenChange(false)
      router.push(`/dashboard/patients/${patientId}/notes/${note.id}`)
    } catch (err) {
      // A value the type no longer takes (an option since removed, say) is
      // shown beside the inputs rather than behind the dialog.
      if (inputs && err instanceof ApiError && err.code === "INVALID_NOTE_INPUTS") {
        setInputsRejected(true)
        return
      }
      const message =
        err instanceof Error
          ? err.message
          : `Could not create ${type.label} note. Please try again.`
      showToast(message, "error")
    }
  }

  const handlePick = (type: NoteTypeSchema) => {
    if (type.is_locked) {
      showToast(lockedNoteTypeMessage(type.label), "info")
      return
    }
    if (draftInputs(type).length > 0) {
      choose(type)
      return
    }
    void create(type, null)
  }

  const handleCreate = () => {
    if (!picked) return
    if (missingInputs(pickedInputs, inputValues).length > 0) {
      setShowMissing(true)
      return
    }
    setInputsRejected(false)
    void create(picked, filledInputs(pickedInputs, inputValues))
  }

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="w-4 h-4 mr-2" />
          New note
        </Button>
      </DialogTrigger>
      <DialogContent>
        {picked ? (
          <>
            <DialogHeader>
              <DialogTitle>{picked.label}</DialogTitle>
              <DialogDescription>{picked.description}</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 pt-2">
              <LabeledNoteInputs
                inputs={pickedInputs}
                values={inputValues}
                onChange={(key, value) =>
                  setInputValues((prev) => ({ ...prev, [key]: value }))
                }
                missing={missing}
              />
              {inputsRejected && (
                <p role="alert" className="text-sm text-destructive">
                  These note details weren&apos;t accepted. Check them and try again.
                </p>
              )}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => choose(null)}>
                Back
              </Button>
              <Button type="button" onClick={handleCreate} disabled={createNote.isPending}>
                Create
              </Button>
            </DialogFooter>
          </>
        ) : (
        <>
        <DialogHeader>
          <DialogTitle>New note</DialogTitle>
          <DialogDescription>
            Start from a session transcript, or create a blank note to fill in
            yourself. Everything is saved against this {people.one}.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              setTranscriptOpen(true)
            }}
            className="w-full text-left rounded-lg border border-neutral-200 p-4 hover:border-primary-400 hover:bg-primary-50/40 transition-colors"
          >
            <div className="flex items-start gap-3">
              <Upload className="w-5 h-5 text-primary-600 mt-0.5 shrink-0" />
              <div className="flex-1">
                <div className="font-medium text-neutral-900">
                  From a transcript
                </div>
                <div className="text-sm text-neutral-600">
                  Upload a session transcript (VTT, JSON, or TXT) to draft a
                  note.
                </div>
              </div>
            </div>
          </button>

          <button
            type="button"
            onClick={() => {
              setOpen(false)
              setImportOpen(true)
            }}
            className="w-full text-left rounded-lg border border-neutral-200 p-4 hover:border-primary-400 hover:bg-primary-50/40 transition-colors"
          >
            <div className="flex items-start gap-3">
              <FileUp className="w-5 h-5 text-primary-600 mt-0.5 shrink-0" />
              <div className="flex-1">
                <div className="font-medium text-neutral-900">
                  Import existing notes
                </div>
                <div className="text-sm text-neutral-600">
                  Upload one or many notes you&apos;ve already written (PDF,
                  Word, or TXT) for your review.
                </div>
              </div>
            </div>
          </button>

          <div className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-neutral-500">
              Or start blank
            </div>
            {isLoading && (
              <p className="text-sm text-neutral-500">Loading note types…</p>
            )}
            {!isLoading && sessionTypes.length === 0 && (
              <p className="text-sm text-neutral-500">
                No note types available.
              </p>
            )}
            {sessionTypes.map((type) => {
            const locked = !!type.is_locked
            return (
              <button
                key={type.key}
                type="button"
                onClick={() => handlePick(type)}
                disabled={createNote.isPending}
                aria-disabled={locked}
                data-locked={locked || undefined}
                className={
                  locked
                    ? "w-full text-left rounded-lg border border-amber-200 bg-amber-50/40 p-4 hover:border-amber-400 hover:bg-amber-50 transition-colors disabled:opacity-50 cursor-pointer"
                    : "w-full text-left rounded-lg border border-neutral-200 p-4 hover:border-primary-400 hover:bg-primary-50/40 transition-colors disabled:opacity-50"
                }
              >
                <div className="flex items-start gap-3">
                  {locked ? (
                    <Lock className="w-5 h-5 text-amber-600 mt-0.5 shrink-0" />
                  ) : (
                    <FileText className="w-5 h-5 text-primary-600 mt-0.5 shrink-0" />
                  )}
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <div className="font-medium text-neutral-900">
                        {type.label}
                      </div>
                      {locked && (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-800 bg-amber-100 px-2 py-0.5 rounded">
                          <Sparkles className="w-3 h-3" />
                          Practice tier
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-neutral-600">
                      {type.description}
                    </div>
                    {type.restricted && (
                      <div className="text-xs text-neutral-500 mt-1">
                        Only you can see this note.
                      </div>
                    )}
                    {locked && (
                      <div className="text-xs text-amber-700 mt-1">
                        Upgrade to unlock {type.label} notes.
                      </div>
                    )}
                  </div>
                </div>
              </button>
              )
            })}
          </div>
        </div>
        </>
        )}
      </DialogContent>
      </Dialog>
      <UploadTranscriptDialog
        patientId={patientId}
        open={transcriptOpen}
        onOpenChange={setTranscriptOpen}
      />
      <ImportNotesDialog
        patientId={patientId}
        open={importOpen}
        onOpenChange={setImportOpen}
      />
    </>
  )
}
