// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useResolveNoteTypeSpec, useSavePracticeNoteType } from "@/hooks/useNoteTypes"
import type { NoteTypeSchema, PracticeNoteTypeSpec } from "@/types/noteTypes"
import { SettingsCard } from "../ui"
import { BaseSections } from "./BaseSections"
import { FieldMessages, Labelled } from "./EditorParts"
import { InputsEditor } from "./InputsEditor"
import { TryItPanel } from "./TryItPanel"
import { shapeOf, specFromBasedDraft, type BasedDraft } from "./basedModel"
import { errorsAt, fieldErrorsFrom, slugFor, type FieldErrors } from "./editorModel"

interface BasedNoteTypeEditorProps {
  initial: BasedDraft
  /** Keys already in the catalog, so a new type doesn't take one of them. */
  takenKeys: string[]
  onSaved: (saved: NoteTypeSchema) => void
  onCancel: () => void
  /** Continue in the full editor with this spec: the type as it resolves now, no longer based. */
  onDetach: (spec: PracticeNoteTypeSpec) => void
}

const ADDED_INPUTS = "patch.add_inputs."

/** Messages for the added inputs, addressed the way the inputs editor reads them. */
function inputErrors(errors: FieldErrors): FieldErrors {
  return Object.fromEntries(
    Object.entries(errors)
      .filter(([path]) => path.startsWith(ADDED_INPUTS))
      .map(([path, messages]) => [`inputs.${path.slice(ADDED_INPUTS.length)}`, messages]),
  )
}

/**
 * Adjust a built-in note type: hide what the practice doesn't use, add its
 * own fields, sections and appointment details, and add instructions. Only
 * the changes are saved, so the type keeps every later improvement to its
 * base. Detach turns it into a full copy for anyone who wants total control.
 */
export function BasedNoteTypeEditor({ initial, takenKeys, onSaved, onCancel, onDetach }: BasedNoteTypeEditorProps) {
  const [draft, setDraft] = useState(initial)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [confirmingDetach, setConfirmingDetach] = useState(false)
  const save = useSavePracticeNoteType()
  const resolve = useResolveNoteTypeSpec()
  const spec = specFromBasedDraft(draft)
  const base = draft.base

  const handleSave = () => {
    setErrors({})
    save.mutate(
      { slug: slugFor(draft, takenKeys), spec },
      { onSuccess: onSaved, onError: (error) => setErrors(fieldErrorsFrom(error)) },
    )
  }
  const handleDetach = () => {
    setErrors({})
    resolve.mutate(spec, {
      onSuccess: (resolved) => onDetach(resolved.spec),
      onError: (error) => {
        setConfirmingDetach(false)
        setErrors(fieldErrorsFrom(error))
      },
    })
  }

  const shownElsewhere = (path: string) => path === "label" || path === "description" || path.startsWith(ADDED_INPUTS)
  const otherMessages = Object.entries(errors)
    .filter(([path]) => !shownElsewhere(path))
    .flatMap(([, messages]) => messages)

  return (
    <>
      <SettingsCard title={draft.slug ? `Edit ${initial.label}` : "New note type"} description={`Based on ${base.label}`}>
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

      <SettingsCard title="Sections and fields" description="Hide what you don't use and add what you need.">
        <BaseSections draft={draft} onChange={setDraft} />
      </SettingsCard>

      <SettingsCard title="Appointment details" description="Filled in when you schedule a session with this note type.">
        {base.spec.inputs.length > 0 && (
          <ul className="mb-3 space-y-0.5 text-[13px] text-muted-foreground">
            {base.spec.inputs.map((input) => (
              <li key={input.key}>{input.label}</li>
            ))}
          </ul>
        )}
        <InputsEditor
          inputs={draft.addedInputs}
          errors={inputErrors(errors)}
          onChange={(addedInputs) => setDraft({ ...draft, addedInputs })}
        />
      </SettingsCard>

      <SettingsCard title="Instructions" description="Anything else the draft should do, in your own words.">
        <Labelled label="Instructions to add" hint="optional" messages={errorsAt(errors, "patch.system_prompt_append")}>
          {(props) => (
            <Textarea
              {...props}
              rows={3}
              value={draft.instructions}
              onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
            />
          )}
        </Labelled>
      </SettingsCard>

      <SettingsCard title="Try it" description="See a draft with the note type as it stands, before you save it.">
        <TryItPanel spec={spec} shape={shapeOf(spec, base)} samples={base.samples} onInvalid={setErrors} />
      </SettingsCard>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={handleSave} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save note type"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        {confirmingDetach ? (
          <div role="group" aria-label="Detach" className="flex flex-wrap items-center gap-2">
            <p className="text-[12.5px] text-muted-foreground">
              Detach from {base.label}? You can then edit everything, and changes to {base.label} won&apos;t reach it.
            </p>
            <Button type="button" size="sm" variant="outline" disabled={resolve.isPending} onClick={handleDetach}>
              Detach
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirmingDetach(false)}>
              Keep it based
            </Button>
          </div>
        ) : (
          <Button type="button" variant="ghost" onClick={() => setConfirmingDetach(true)}>
            Detach from {base.label}
          </Button>
        )}
        {Object.keys(errors).length > 0 && (
          <div role="alert">
            {otherMessages.length > 0 ? (
              <FieldMessages messages={otherMessages} />
            ) : (
              <p className="text-[12px] text-red-700">Fix the highlighted parts, then save again.</p>
            )}
          </div>
        )}
      </div>
    </>
  )
}
