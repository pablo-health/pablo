// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { FieldMessages, Labelled, RowActions, SELECT_CLASS } from "./EditorParts"
import {
  blankField,
  blankSection,
  errorsAt,
  move,
  type DraftField,
  type DraftSection,
  type FieldErrors,
} from "./editorModel"

interface SectionsEditorProps {
  sections: DraftSection[]
  errors: FieldErrors
  onChange: (sections: DraftSection[]) => void
}

/** The note's sections, each with its fields, in the order the note shows them. */
export function SectionsEditor({ sections, errors, onChange }: SectionsEditorProps) {
  const update = (index: number, patch: Partial<DraftSection>) =>
    onChange(sections.map((s, i) => (i === index ? { ...s, ...patch } : s)))

  return (
    <div className="space-y-4">
      <FieldMessages messages={errorsAt(errors, "sections")} />
      {sections.map((section, si) => {
        const path = `sections.${si}`
        const name = section.label || `section ${si + 1}`
        return (
          <div
            key={section.uid}
            role="group"
            aria-label={`Section ${si + 1}`}
            className="rounded-xl border border-border bg-foreground/[0.02] p-4"
          >
            <div className="flex items-start gap-2">
              <Labelled
                label="Section name"
                className="flex-1"
                messages={errorsAt(errors, `${path}.label`, `${path}.key`, path)}
              >
                {(props) => (
                  <Input {...props} value={section.label} onChange={(e) => update(si, { label: e.target.value })} />
                )}
              </Labelled>
              <div className="pt-5">
                <RowActions
                  name={name}
                  index={si}
                  count={sections.length}
                  canRemove={sections.length > 1}
                  onMove={(delta) => onChange(move(sections, si, delta))}
                  onRemove={() => onChange(sections.filter((_, i) => i !== si))}
                />
              </div>
            </div>
            <FieldsEditor
              fields={section.fields}
              path={path}
              errors={errors}
              onChange={(fields) => update(si, { fields })}
            />
          </div>
        )
      })}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...sections, blankSection()])}>
        <Plus aria-hidden="true" />
        Add section
      </Button>
    </div>
  )
}

function FieldsEditor({
  fields,
  path,
  errors,
  onChange,
}: {
  fields: DraftField[]
  path: string
  errors: FieldErrors
  onChange: (fields: DraftField[]) => void
}) {
  const update = (index: number, patch: Partial<DraftField>) =>
    onChange(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)))

  return (
    <div className="mt-3 space-y-3 border-l-2 border-border pl-4">
      <FieldMessages messages={errorsAt(errors, `${path}.fields`)} />
      {fields.map((field, fi) => {
        const fieldPath = `${path}.fields.${fi}`
        return (
          <div key={field.uid} role="group" aria-label={`Field ${fi + 1}`} className="space-y-2">
            <div className="flex items-start gap-2">
              <Labelled
                label="Field name"
                className="flex-1"
                messages={errorsAt(errors, `${fieldPath}.label`, `${fieldPath}.key`, fieldPath)}
              >
                {(props) => (
                  <Input {...props} value={field.label} onChange={(e) => update(fi, { label: e.target.value })} />
                )}
              </Labelled>
              <Labelled label="Shape" className="w-36" messages={errorsAt(errors, `${fieldPath}.kind`)}>
                {(props) => (
                  <select
                    {...props}
                    value={field.kind}
                    onChange={(e) => update(fi, { kind: e.target.value === "list" ? "list" : "text" })}
                    className={SELECT_CLASS}
                  >
                    <option value="text">Paragraph</option>
                    <option value="list">List</option>
                  </select>
                )}
              </Labelled>
              <div className="pt-5">
                <RowActions
                  name={field.label || `field ${fi + 1}`}
                  index={fi}
                  count={fields.length}
                  canRemove={fields.length > 1}
                  onMove={(delta) => onChange(move(fields, fi, delta))}
                  onRemove={() => onChange(fields.filter((_, i) => i !== fi))}
                />
              </div>
            </div>
            <Labelled label="What goes here" messages={errorsAt(errors, `${fieldPath}.ai_hint`)}>
              {(props) => (
                <Textarea
                  {...props}
                  rows={2}
                  className="min-h-[56px]"
                  value={field.ai_hint}
                  onChange={(e) => update(fi, { ai_hint: e.target.value })}
                />
              )}
            </Labelled>
          </div>
        )
      })}
      <Button type="button" variant="ghost" size="sm" onClick={() => onChange([...fields, blankField()])}>
        <Plus aria-hidden="true" />
        Add field
      </Button>
    </div>
  )
}
