// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { useDeriveNoteType, useDeriveReferences } from "@/hooks/useNoteTypes"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { DeriveNoteTypeResponse, NoteTypeSchema } from "@/types/noteTypes"
import { SettingsCard } from "../ui"
import { FieldMessages, Labelled, SELECT_CLASS } from "./EditorParts"

/** The derive route's limit on samples, pasted and uploaded together. */
const MAX_NOTES = 3

interface FromYourNotesProps {
  /** The catalog; any of its types can be the comparison. */
  noteTypes: NoteTypeSchema[]
  onDerived: (derived: DeriveNoteTypeResponse) => void
  onCancel: () => void
}

/**
 * Propose a note type from up to three of the clinician's notes and/or a
 * description of how they write them. The proposal opens in the editor;
 * nothing is saved until the clinician saves it there.
 *
 * The samples are a client's record. The route reads them for this one call
 * and keeps none of their text, which is the one thing the copy says about it.
 */
export function FromYourNotes({ noteTypes, onDerived, onCancel }: FromYourNotesProps) {
  const people = usePeopleTerm()
  const [pasted, setPasted] = useState<string[]>([""])
  const [files, setFiles] = useState<File[]>([])
  const [description, setDescription] = useState("")
  const [reference, setReference] = useState("")
  const derive = useDeriveNoteType()
  const { data: registered } = useDeriveReferences()

  const samples = pasted.filter((text) => text.trim())
  const noteCount = samples.length + files.length
  const tooMany = noteCount > MAX_NOTES
  const canPropose = (noteCount > 0 || description.trim() !== "") && !tooMany && !derive.isPending

  const propose = () => {
    derive.mutate(
      { samples, files, description, reference: reference || null },
      { onSuccess: onDerived },
    )
  }

  return (
    <SettingsCard
      title="From your notes"
      description="Paste or upload up to three of your notes, or describe how you write them. Pablo proposes a note type in the same shape. Your notes are used for this and not kept."
    >
      <div className="space-y-3">
        {pasted.map((text, index) => (
          <Labelled key={index} label={`Note ${index + 1}`} messages={[]}>
            {(props) => (
              <Textarea
                {...props}
                rows={6}
                value={text}
                onChange={(e) => setPasted(pasted.map((t, i) => (i === index ? e.target.value : t)))}
              />
            )}
          </Labelled>
        ))}
        {pasted.length + files.length < MAX_NOTES && (
          <Button type="button" variant="ghost" size="sm" onClick={() => setPasted([...pasted, ""])}>
            <Plus aria-hidden="true" />
            Add another note
          </Button>
        )}

        <Labelled label="Upload notes" hint="PDF, Word or text, one note per file" messages={[]}>
          {(props) => (
            <input
              {...props}
              type="file"
              multiple
              accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
              className="block text-[12.5px]"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          )}
        </Labelled>

        <Labelled label="How you write your notes" hint="optional" messages={[]}>
          {(props) => (
            <Textarea
              {...props}
              rows={3}
              placeholder={`For example: what the ${people.one} reports, what I observe, then the plan.`}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Labelled>

        <Labelled label="Compare with" hint="optional" messages={[]}>
          {(props) => (
            <select {...props} value={reference} onChange={(e) => setReference(e.target.value)} className={SELECT_CLASS}>
              <option value="">Nothing</option>
              {(registered?.references ?? []).map((r) => (
                <option key={r.key} value={r.key}>
                  {r.label}
                </option>
              ))}
              {noteTypes.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          )}
        </Labelled>

        <FieldMessages messages={tooMany ? [`Use up to ${MAX_NOTES} notes.`] : []} />

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Button type="button" onClick={propose} disabled={!canPropose}>
            {derive.isPending ? "Proposing… this can take a minute" : "Propose a note type"}
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          {derive.error && !derive.isPending && (
            <p role="alert" className="text-[12.5px] text-red-700">
              {derive.error.message || "A note type couldn't be proposed. Try again."}
            </p>
          )}
        </div>
      </div>
    </SettingsCard>
  )
}
