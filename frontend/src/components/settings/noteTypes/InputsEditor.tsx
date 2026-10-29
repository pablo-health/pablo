// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { FieldMessages, Labelled, RowActions, SELECT_CLASS } from "./EditorParts"
import { blankInput, errorsAt, move, type DraftInput, type FieldErrors } from "./editorModel"

interface InputsEditorProps {
  inputs: DraftInput[]
  errors: FieldErrors
  onChange: (inputs: DraftInput[]) => void
}

/** Values filled in on the appointment, before the visit, that the draft uses. */
export function InputsEditor({ inputs, errors, onChange }: InputsEditorProps) {
  const update = (index: number, patch: Partial<DraftInput>) =>
    onChange(inputs.map((input, i) => (i === index ? { ...input, ...patch } : input)))

  return (
    <div className="space-y-3">
      <FieldMessages messages={errorsAt(errors, "inputs")} />
      {inputs.map((input, ii) => {
        const path = `inputs.${ii}`
        return (
          <div
            key={input.uid}
            role="group"
            aria-label={`Appointment detail ${ii + 1}`}
            className="space-y-2 rounded-xl border border-border p-3"
          >
            <div className="flex items-start gap-2">
              <Labelled label="Name" className="flex-1" messages={errorsAt(errors, `${path}.label`, `${path}.key`)}>
                {(props) => (
                  <Input {...props} value={input.label} onChange={(e) => update(ii, { label: e.target.value })} />
                )}
              </Labelled>
              <Labelled label="Answer" className="w-40" messages={errorsAt(errors, `${path}.kind`)}>
                {(props) => (
                  <select
                    {...props}
                    value={input.kind}
                    onChange={(e) => update(ii, { kind: e.target.value === "choice" ? "choice" : "text" })}
                    className={SELECT_CLASS}
                  >
                    <option value="text">Free text</option>
                    <option value="choice">One of a list</option>
                  </select>
                )}
              </Labelled>
              <div className="pt-5">
                <RowActions
                  name={input.label || `detail ${ii + 1}`}
                  index={ii}
                  count={inputs.length}
                  onMove={(delta) => onChange(move(inputs, ii, delta))}
                  onRemove={() => onChange(inputs.filter((_, i) => i !== ii))}
                />
              </div>
            </div>
            {input.kind === "choice" && (
              <Labelled label="Choices" hint="one per line" messages={errorsAt(errors, `${path}.options`)}>
                {(props) => (
                  <Textarea
                    {...props}
                    rows={2}
                    className="min-h-[56px]"
                    value={input.options.join("\n")}
                    onChange={(e) => update(ii, { options: e.target.value.split("\n") })}
                  />
                )}
              </Labelled>
            )}
            <label className="flex items-center gap-2 text-[12.5px] text-foreground">
              <input
                type="checkbox"
                checked={input.required}
                onChange={(e) => update(ii, { required: e.target.checked })}
              />
              Required before scheduling
            </label>
            <FieldMessages messages={errorsAt(errors, path)} />
          </div>
        )
      })}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...inputs, blankInput()])}>
        <Plus aria-hidden="true" />
        Add appointment detail
      </Button>
    </div>
  )
}
