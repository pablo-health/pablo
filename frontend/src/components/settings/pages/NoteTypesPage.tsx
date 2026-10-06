// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { useNoteType, useNoteTypes } from "@/hooks/useNoteTypes"
import { PRACTICE_KEY_PREFIX, type NoteTypeSchema } from "@/types/noteTypes"
import { SettingsCard } from "../ui"
import { NoteTypeEditor } from "../noteTypes/NoteTypeEditor"
import { NoteTypeList } from "../noteTypes/NoteTypeList"
import { StartOptions } from "../noteTypes/StartOptions"
import { blankDraft, draftFromSpec, type NoteTypeDraft } from "../noteTypes/editorModel"
import { NOTE_TYPE_TEMPLATES, type SampleVisit } from "../noteTypes/templates"

type Mode =
  | { kind: "list" }
  | { kind: "edit"; key: string }
  | { kind: "new"; draft: NoteTypeDraft; samples: SampleVisit[] }

/** A type saved from a template keeps that template's sample visits for Try it. */
function samplesForSlug(slug: string): SampleVisit[] {
  const template = NOTE_TYPE_TEMPLATES.find((t) => slug === t.slug || slug.startsWith(`${t.slug}_`))
  return template?.samples ?? []
}

/**
 * Practice > Note types. The practice's own note types: list and retire them,
 * edit one, or start one blank, from a template, or from JSON.
 */
export function NoteTypesPage() {
  const [mode, setMode] = useState<Mode>({ kind: "list" })
  const [savedMessage, setSavedMessage] = useState<string | null>(null)
  const { data, isLoading, isError } = useNoteTypes()
  const noteTypes = data?.note_types ?? []
  const takenKeys = noteTypes.map((t) => t.key)

  const onSaved = (saved: NoteTypeSchema) => {
    setSavedMessage(`Saved ${saved.label}, version ${saved.version}.`)
    setMode({ kind: "list" })
  }
  const back = () => setMode({ kind: "list" })
  const startNew = (draft: NoteTypeDraft, samples: SampleVisit[] = []) => {
    setSavedMessage(null)
    setMode({ kind: "new", draft, samples })
  }

  if (mode.kind === "new") {
    return (
      <NoteTypeEditor
        initial={mode.draft}
        samples={mode.samples}
        takenKeys={takenKeys}
        onSaved={onSaved}
        onCancel={back}
      />
    )
  }
  if (mode.kind === "edit") {
    return <EditExisting noteTypeKey={mode.key} takenKeys={takenKeys} onSaved={onSaved} onCancel={back} />
  }

  if (isLoading) return null
  if (isError) {
    return (
      <SettingsCard>
        <p role="alert" className="text-sm text-muted-foreground">
          Your note types couldn&apos;t be loaded. Try again.
        </p>
      </SettingsCard>
    )
  }

  return (
    <>
      {savedMessage && (
        <p role="status" className="mb-3 text-[12.5px] font-semibold text-secondary-600">
          {savedMessage}
        </p>
      )}
      <NoteTypeList
        noteTypes={noteTypes}
        onEdit={(key) => {
          setSavedMessage(null)
          setMode({ kind: "edit", key })
        }}
      />
      <StartOptions
        onBlank={() => startNew(blankDraft())}
        onTemplate={(t) => startNew(draftFromSpec(t.spec, null, t.slug), t.samples)}
        onImport={(spec) => startNew(draftFromSpec(spec, null))}
      />
    </>
  )
}

function EditExisting({
  noteTypeKey,
  takenKeys,
  onSaved,
  onCancel,
}: {
  noteTypeKey: string
  takenKeys: string[]
  onSaved: (saved: NoteTypeSchema) => void
  onCancel: () => void
}) {
  const { data, isLoading } = useNoteType(noteTypeKey)
  const slug = noteTypeKey.slice(PRACTICE_KEY_PREFIX.length)

  if (isLoading) return null
  if (!data?.spec) {
    return (
      <SettingsCard>
        <p role="alert" className="text-sm text-muted-foreground">
          This note type couldn&apos;t be loaded. Try again.
        </p>
      </SettingsCard>
    )
  }
  return (
    <NoteTypeEditor
      initial={draftFromSpec(data.spec, slug)}
      samples={samplesForSlug(slug)}
      takenKeys={takenKeys}
      onSaved={onSaved}
      onCancel={onCancel}
    />
  )
}
