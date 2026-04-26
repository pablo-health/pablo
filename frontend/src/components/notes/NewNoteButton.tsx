// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * NewNoteButton
 *
 * Patient-scoped entry point for creating a standalone clinical note (no
 * recording session). Opens a picker filtered to ``context=session`` types
 * (SOAP, Narrative, ...), POSTs to ``/api/patients/{pid}/notes`` with empty
 * content, and routes the user to the new note in edit mode.
 */

"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { FileText, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { useNoteTypes } from "@/hooks/useNoteTypes"
import { useCreateStandaloneNote } from "@/hooks/useNotes"
import type { NoteTypeSchema } from "@/types/noteTypes"

export interface NewNoteButtonProps {
  patientId: string
}

export function NewNoteButton({ patientId }: NewNoteButtonProps) {
  const [open, setOpen] = useState(false)
  const router = useRouter()
  const { data: catalog, isLoading } = useNoteTypes()
  const createNote = useCreateStandaloneNote()

  const sessionTypes = (catalog?.note_types ?? []).filter(
    (t) => t.context === "session",
  )

  const handlePick = async (type: NoteTypeSchema) => {
    const note = await createNote.mutateAsync({
      patientId,
      data: { note_type: type.key },
    })
    setOpen(false)
    router.push(`/dashboard/patients/${patientId}/notes/${note.id}`)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="w-4 h-4 mr-2" />
          New note
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New clinical note</DialogTitle>
          <DialogDescription>
            Choose a note type. Notes are saved against the current patient and
            can be edited until you finalize them.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 pt-2">
          {isLoading && (
            <p className="text-sm text-neutral-500">Loading note types…</p>
          )}
          {!isLoading && sessionTypes.length === 0 && (
            <p className="text-sm text-neutral-500">
              No note types available.
            </p>
          )}
          {sessionTypes.map((type) => (
            <button
              key={type.key}
              type="button"
              onClick={() => handlePick(type)}
              disabled={createNote.isPending}
              className="w-full text-left rounded-lg border border-neutral-200 p-4 hover:border-primary-400 hover:bg-primary-50/40 transition-colors disabled:opacity-50"
            >
              <div className="flex items-start gap-3">
                <FileText className="w-5 h-5 text-primary-600 mt-0.5 shrink-0" />
                <div>
                  <div className="font-medium text-neutral-900">
                    {type.label}
                  </div>
                  <div className="text-sm text-neutral-600">
                    {type.description}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
