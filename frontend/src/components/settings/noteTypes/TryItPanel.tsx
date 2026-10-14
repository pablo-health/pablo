// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { SchemaNoteBody } from "@/components/sessions/SchemaNoteView"
import { usePreviewNoteDraft } from "@/hooks/useNoteTypes"
import { useSessionList } from "@/hooks/useSessions"
import { ApiError } from "@/lib/api/client"
import { isReviewInput } from "@/lib/mdm"
import type { NoteDraftPreviewRequest, PracticeNoteTypeSpec, SampleVisit } from "@/types/noteTypes"
import { SegmentedControl, type SegmentedOption } from "../ui"
import { Labelled, SELECT_CLASS } from "./EditorParts"
import { definitionFromDraft, fieldErrorsFrom, type FieldErrors } from "./editorModel"

type Source = "sample" | "session" | "paste"

interface TryItPanelProps {
  /** The type as it stands in the editor, saved or not. */
  spec: PracticeNoteTypeSpec
  /** Its sections and inputs, when `spec` takes them from a base. */
  shape?: PracticeNoteTypeSpec
  samples: SampleVisit[]
  /** A definition the server refused, by field, for the editor to show. */
  onInvalid: (errors: FieldErrors) => void
}

/**
 * Draft a note with the type as edited, from a sample visit, one of the
 * clinician's recorded sessions, or pasted text. The preview route writes
 * nothing, so this never touches a session or its note.
 */
export function TryItPanel({ spec, shape = spec, samples, onInvalid }: TryItPanelProps) {
  /** The shape as it was when the shown draft was made, so later edits don't relabel it. */
  const [drafted, setDrafted] = useState<PracticeNoteTypeSpec | null>(null)
  const [source, setSource] = useState<Source>(samples.length > 0 ? "sample" : "paste")
  const [sampleId, setSampleId] = useState(samples[0]?.id ?? "")
  const [sessionId, setSessionId] = useState("")
  const [pasted, setPasted] = useState("")
  const [inputs, setInputs] = useState<Record<string, string>>({})
  // The medical decision making choices are made at review and never reach a draft.
  const draftInputs = shape.inputs.filter((input) => !isReviewInput(input.key))
  const preview = usePreviewNoteDraft()
  const { data: sessionList } = useSessionList(undefined, { enabled: source === "session" })

  const sessions = (sessionList?.data ?? []).filter((s) => s.transcript?.content?.trim())
  const options: SegmentedOption<Source>[] = [
    ...(samples.length > 0 ? [{ value: "sample" as const, label: "Sample visit" }] : []),
    { value: "session", label: "One of your sessions" },
    { value: "paste", label: "Paste a transcript" },
  ]

  const transcript = ((): NoteDraftPreviewRequest["transcript"] | null => {
    if (source === "sample") {
      const sample = samples.find((s) => s.id === sampleId)
      return sample ? { format: "txt", content: sample.transcript } : null
    }
    if (source === "session") {
      const session = sessions.find((s) => s.id === sessionId)
      return session ? { format: session.transcript.format, content: session.transcript.content } : null
    }
    return pasted.trim() ? { format: "txt", content: pasted } : null
  })()

  const run = () => {
    if (!transcript) return
    const filled = Object.fromEntries(
      draftInputs.map((i) => [i.key, (inputs[i.key] ?? "").trim()]).filter(([, v]) => v),
    )
    setDrafted(shape)
    preview.mutate(
      { spec, transcript, inputs: filled },
      {
        onSuccess: () => onInvalid({}),
        onError: (error) => {
          if (error instanceof ApiError && error.details?.validation) onInvalid(fieldErrorsFrom(error, ["body", "spec"]))
        },
      },
    )
  }

  const failure = preview.error
  const failureMessage =
    failure instanceof ApiError && failure.details?.validation
      ? "Fix the highlighted parts of the note type first."
      : failure instanceof Error
        ? failure.message
        : null

  return (
    <div className="space-y-4">
      <SegmentedControl label="Draft from" value={source} onChange={setSource} options={options} />

      {source === "sample" && samples.length > 1 && (
        <Labelled label="Sample visit" messages={[]}>
          {(props) => (
            <select {...props} value={sampleId} onChange={(e) => setSampleId(e.target.value)} className={SELECT_CLASS}>
              {samples.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
        </Labelled>
      )}
      {source === "session" && (
        <Labelled label="Session" hint="its note stays as it is" messages={[]}>
          {(props) => (
            <select {...props} value={sessionId} onChange={(e) => setSessionId(e.target.value)} className={SELECT_CLASS}>
              <option value="">{sessions.length ? "Choose a session…" : "No recorded sessions yet"}</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.patient_name} · {new Date(s.session_date).toLocaleDateString()}
                </option>
              ))}
            </select>
          )}
        </Labelled>
      )}
      {source === "paste" && (
        <Labelled label="Transcript" messages={[]}>
          {(props) => <Textarea {...props} rows={6} value={pasted} onChange={(e) => setPasted(e.target.value)} />}
        </Labelled>
      )}

      {draftInputs.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {draftInputs.map((input) => (
            <Labelled key={input.key} label={input.label || "Untitled detail"} hint={input.required ? "Required" : undefined} messages={[]}>
              {(props) =>
                input.kind === "choice" ? (
                  <select
                    {...props}
                    value={inputs[input.key] ?? ""}
                    onChange={(e) => setInputs({ ...inputs, [input.key]: e.target.value })}
                    className={SELECT_CLASS}
                  >
                    <option value="">Choose…</option>
                    {input.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    {...props}
                    value={inputs[input.key] ?? ""}
                    onChange={(e) => setInputs({ ...inputs, [input.key]: e.target.value })}
                  />
                )
              }
            </Labelled>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3">
        <Button type="button" variant="outline" onClick={run} disabled={!transcript || preview.isPending}>
          {preview.isPending ? "Drafting…" : "Draft a note"}
        </Button>
        {failureMessage && !preview.isPending && (
          <p role="alert" className="text-[12.5px] text-red-700">
            {failureMessage}
          </p>
        )}
      </div>

      {preview.data && !preview.isPending && (
        <div data-testid="try-it-draft">
          <SchemaNoteBody
            definition={definitionFromDraft(drafted ?? shape)}
            note={{ note_type: "schema", key: preview.data.key, sections: preview.data.sections }}
            noteEdited={null}
            readonly
          />
        </div>
      )}
    </div>
  )
}
