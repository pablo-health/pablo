// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useSavePracticeNoteType } from "@/hooks/useNoteTypes"
import type { DeriveNoteTypeResponse, DeriveSuggestion, NoteTypeSchema } from "@/types/noteTypes"
import { SettingsCard } from "../ui"
import { DerivedFindings } from "./DerivedFindings"
import { FieldMessages, Labelled } from "./EditorParts"
import { InputsEditor } from "./InputsEditor"
import { SectionsEditor } from "./SectionsEditor"
import { TryItPanel } from "./TryItPanel"
import {
  blankField,
  blankSection,
  errorsAt,
  fieldErrorsFrom,
  slugFor,
  specFromDraft,
  type FieldErrors,
  type NoteTypeDraft,
} from "./editorModel"
import type { SampleVisit } from "@/types/noteTypes"

interface NoteTypeEditorProps {
  initial: NoteTypeDraft
  samples: SampleVisit[]
  /** Keys already in the catalog, so a new type doesn't take one of them. */
  takenKeys: string[]
  onSaved: (saved: NoteTypeSchema) => void
  onCancel: () => void
  /** A proposal from the clinician's notes, with what it left out. */
  derived?: DeriveNoteTypeResponse
}

/**
 * Edit a practice note type: its name, sections and fields with what goes in
 * each, and the details asked on the appointment. Saving writes a new version;
 * notes already written keep the version they were written with.
 *
 * The prompts the type was loaded with (a template's, or a saved type's) are
 * carried through untouched — they are not edited here.
 */
export function NoteTypeEditor({ initial, samples, takenKeys, onSaved, onCancel, derived }: NoteTypeEditorProps) {
  const [draft, setDraft] = useState(initial)
  const [errors, setErrors] = useState<FieldErrors>({})
  /** The field just added for an unplaced passage, so its name box takes focus. */
  const [focusUid, setFocusUid] = useState<string | undefined>()
  const save = useSavePracticeNoteType()
  const spec = specFromDraft(draft)

  const handleSave = () => {
    setErrors({})
    save.mutate(
      { slug: slugFor(draft, takenKeys), spec },
      {
        onSuccess: onSaved,
        onError: (error) => setErrors(fieldErrorsFrom(error)),
      },
    )
  }

  const hasErrors = Object.keys(errors).length > 0

  // Into the last section: it is usually where a note's loose ends go, and the
  // clinician can move the field from there.
  const addField = () => {
    const field = blankField()
    const last = draft.sections.length - 1
    setDraft({
      ...draft,
      sections: draft.sections.map((s, i) => (i === last ? { ...s, fields: [...s.fields, field] } : s)),
    })
    setFocusUid(field.uid)
    return draft.sections[last]?.label || `section ${last + 1}`
  }
  const addSection = ({ label, description }: DeriveSuggestion) => {
    const section = blankSection()
    setDraft({
      ...draft,
      sections: [...draft.sections, { ...section, label, fields: [{ ...section.fields[0], label, ai_hint: description }] }],
    })
  }

  return (
    <>
      <SettingsCard title={draft.slug ? `Edit ${initial.label}` : "New note type"}>
        <div className="space-y-3">
          <Labelled label="Note type name" messages={errorsAt(errors, "label")}>
            {(props) => <Input {...props} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />}
          </Labelled>
          <Labelled label="Description" hint="optional" messages={errorsAt(errors, "description")}>
            {(props) => (
              <Textarea
                {...props}
                rows={2}
                className="min-h-[56px]"
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            )}
          </Labelled>
        </div>
      </SettingsCard>

      {derived && <DerivedFindings derived={derived} onAddField={addField} onAddSection={addSection} />}

      <SettingsCard title="Sections and fields" description="What goes in each field guides the draft.">
        <SectionsEditor
          sections={draft.sections}
          errors={errors}
          focusUid={focusUid}
          onChange={(sections) => setDraft({ ...draft, sections })}
        />
      </SettingsCard>

      <SettingsCard title="Appointment details" description="Filled in when you schedule a session with this note type.">
        <InputsEditor inputs={draft.inputs} errors={errors} onChange={(inputs) => setDraft({ ...draft, inputs })} />
      </SettingsCard>

      <SettingsCard title="Try it" description="See a draft with the note type as it stands, before you save it.">
        <TryItPanel spec={spec} samples={samples} onInvalid={setErrors} />
      </SettingsCard>

      <div className="mb-6 flex items-center gap-3">
        <Button type="button" onClick={handleSave} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save note type"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        {hasErrors && (
          <div role="alert">
            {errors[""] ? (
              <FieldMessages messages={errors[""]} />
            ) : (
              <p className="text-[12px] text-red-700">Fix the highlighted parts, then save again.</p>
            )}
          </div>
        )}
      </div>
    </>
  )
}
