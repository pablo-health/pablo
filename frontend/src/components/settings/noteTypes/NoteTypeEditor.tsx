// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useSavePracticeNoteType } from "@/hooks/useNoteTypes"
import type { NoteTypeSchema } from "@/types/noteTypes"
import { SettingsCard } from "../ui"
import { FieldMessages, Labelled } from "./EditorParts"
import { InputsEditor } from "./InputsEditor"
import { SectionsEditor } from "./SectionsEditor"
import { TryItPanel } from "./TryItPanel"
import { errorsAt, fieldErrorsFrom, slugFor, specFromDraft, type FieldErrors, type NoteTypeDraft } from "./editorModel"
import type { SampleVisit } from "./templates"

interface NoteTypeEditorProps {
  initial: NoteTypeDraft
  samples: SampleVisit[]
  /** Keys already in the catalog, so a new type doesn't take one of them. */
  takenKeys: string[]
  onSaved: (saved: NoteTypeSchema) => void
  onCancel: () => void
}

/**
 * Edit a practice note type: its name, sections and fields with what goes in
 * each, and the details asked on the appointment. Saving writes a new version;
 * notes already written keep the version they were written with.
 *
 * The prompts the type was loaded with (a template's, or a saved type's) are
 * carried through untouched — they are not edited here.
 */
export function NoteTypeEditor({ initial, samples, takenKeys, onSaved, onCancel }: NoteTypeEditorProps) {
  const [draft, setDraft] = useState(initial)
  const [errors, setErrors] = useState<FieldErrors>({})
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

      <SettingsCard title="Sections and fields" description="What goes in each field guides the draft.">
        <SectionsEditor sections={draft.sections} errors={errors} onChange={(sections) => setDraft({ ...draft, sections })} />
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
