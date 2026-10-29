// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { useNoteType, useNoteTypeBases, useNoteTypes } from "@/hooks/useNoteTypes"
import {
  PRACTICE_KEY_PREFIX,
  type DeriveNoteTypeResponse,
  type NoteTypeBase,
  type NoteTypeSchema,
  type PracticeNoteTypeSpec,
  type SampleVisit,
} from "@/types/noteTypes"
import { SettingsCard } from "../ui"
import { BasedNoteTypeEditor } from "../noteTypes/BasedNoteTypeEditor"
import { FromYourNotes } from "../noteTypes/FromYourNotes"
import { NoteTypeEditor } from "../noteTypes/NoteTypeEditor"
import { NoteTypeList } from "../noteTypes/NoteTypeList"
import { StartOptions } from "../noteTypes/StartOptions"
import { basedDraftFrom, type BasedDraft } from "../noteTypes/basedModel"
import { blankDraft, draftFromSpec, type NoteTypeDraft } from "../noteTypes/editorModel"

type Mode =
  | { kind: "list" }
  | { kind: "edit"; key: string }
  | { kind: "derive" }
  | { kind: "new"; draft: NoteTypeDraft; samples: SampleVisit[]; derived?: DeriveNoteTypeResponse }
  | { kind: "based"; draft: BasedDraft }

/** A full type saved from a template (or detached from one) keeps its sample visits for Try it. */
function samplesForSlug(bases: NoteTypeBase[], slug: string): SampleVisit[] {
  const base = bases.find((b) => slug === b.slug || slug.startsWith(`${b.slug}_`))
  return base?.samples ?? []
}

/**
 * Practice > Note types. The practice's own note types: list and retire them,
 * edit one, or start one blank, by adjusting a built-in, from the clinician's
 * own notes, or from JSON.
 */
export function NoteTypesPage() {
  const [mode, setMode] = useState<Mode>({ kind: "list" })
  const [savedMessage, setSavedMessage] = useState<string | null>(null)
  const { data, isLoading, isError } = useNoteTypes()
  const { data: basesData } = useNoteTypeBases()
  const bases = basesData?.bases ?? []
  const noteTypes = data?.note_types ?? []
  const takenKeys = noteTypes.map((t) => t.key)

  const onSaved = (saved: NoteTypeSchema) => {
    setSavedMessage(`Saved ${saved.label}, version ${saved.version}.`)
    setMode({ kind: "list" })
  }
  const back = () => setMode({ kind: "list" })
  const startNew = (draft: NoteTypeDraft, samples: SampleVisit[] = [], derived?: DeriveNoteTypeResponse) => {
    setSavedMessage(null)
    setMode({ kind: "new", draft, samples, derived })
  }
  const startBased = (draft: BasedDraft) => {
    setSavedMessage(null)
    setMode({ kind: "based", draft })
  }
  const detach = (draft: BasedDraft) => (spec: PracticeNoteTypeSpec) =>
    startNew(draftFromSpec(spec, draft.slug, draft.preferredSlug), draft.base.samples)

  if (mode.kind === "new") {
    return (
      <NoteTypeEditor
        initial={mode.draft}
        samples={mode.samples}
        takenKeys={takenKeys}
        onSaved={onSaved}
        onCancel={back}
        derived={mode.derived}
      />
    )
  }
  if (mode.kind === "based") {
    return (
      <BasedNoteTypeEditor
        initial={mode.draft}
        takenKeys={takenKeys}
        onSaved={onSaved}
        onCancel={back}
        onDetach={detach(mode.draft)}
      />
    )
  }
  if (mode.kind === "derive") {
    return (
      <FromYourNotes
        noteTypes={noteTypes}
        onDerived={(derived) => startNew(draftFromSpec(derived.spec, null), [], derived)}
        onCancel={back}
      />
    )
  }
  if (mode.kind === "edit") {
    return (
      <EditExisting
        noteTypeKey={mode.key}
        bases={basesData?.bases}
        takenKeys={takenKeys}
        onSaved={onSaved}
        onCancel={back}
        onDetach={(draft, spec) => detach(draft)(spec)}
      />
    )
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
        bases={bases}
        onBlank={() => startNew(blankDraft())}
        onBase={(base) => startBased(basedDraftFrom(base, { preferredSlug: base.slug }))}
        onFromNotes={() => {
          setSavedMessage(null)
          setMode({ kind: "derive" })
        }}
        onImport={(spec) => startNew(draftFromSpec(spec, null))}
      />
    </>
  )
}

function EditExisting({
  noteTypeKey,
  bases,
  takenKeys,
  onSaved,
  onCancel,
  onDetach,
}: {
  noteTypeKey: string
  /** Undefined while they load. */
  bases: NoteTypeBase[] | undefined
  takenKeys: string[]
  onSaved: (saved: NoteTypeSchema) => void
  onCancel: () => void
  onDetach: (draft: BasedDraft, spec: PracticeNoteTypeSpec) => void
}) {
  const { data, isLoading } = useNoteType(noteTypeKey)
  const slug = noteTypeKey.slice(PRACTICE_KEY_PREFIX.length)
  const spec = data?.spec
  const base = spec?.base ? bases?.find((b) => b.key === spec.base) : undefined

  if (isLoading || (spec?.base && !bases)) return null
  if (!spec || (spec.base && !base)) {
    return (
      <SettingsCard>
        <p role="alert" className="text-sm text-muted-foreground">
          This note type couldn&apos;t be loaded. Try again.
        </p>
      </SettingsCard>
    )
  }
  if (base) {
    const draft = basedDraftFrom(base, { spec, slug })
    return (
      <BasedNoteTypeEditor
        initial={draft}
        takenKeys={takenKeys}
        onSaved={onSaved}
        onCancel={onCancel}
        onDetach={(resolved) => onDetach(draft, resolved)}
      />
    )
  }
  return (
    <NoteTypeEditor
      initial={draftFromSpec(spec, slug)}
      samples={samplesForSlug(bases ?? [], slug)}
      takenKeys={takenKeys}
      onSaved={onSaved}
      onCancel={onCancel}
    />
  )
}
