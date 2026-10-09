// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type { PracticeFieldKind, PracticeSectionSpec } from "@/types/noteTypes"
import { FromTheChart, Labelled, SELECT_CLASS } from "./EditorParts"
import { canHide, canHideSection, type AddedDraftField, type BasedDraft } from "./basedModel"
import { blankField, blankSection, type DraftField, type DraftSection } from "./editorModel"

interface BaseSectionsProps {
  draft: BasedDraft
  onChange: (draft: BasedDraft) => void
}

const toggle = (list: string[], item: string) =>
  list.includes(item) ? list.filter((x) => x !== item) : [...list, item]

/**
 * The base's sections, read-only and muted, each field with Hide; the
 * practice's own fields under each section, and its own sections after them.
 */
export function BaseSections({ draft, onChange }: BaseSectionsProps) {
  const setAdded = (section: string, fields: AddedDraftField[]) =>
    onChange({ ...draft, addedFields: { ...draft.addedFields, [section]: fields } })
  const setSections = (addedSections: DraftSection[]) => onChange({ ...draft, addedSections })

  return (
    <div className="space-y-4">
      {draft.base.spec.sections.map((section) => (
        <BaseSection
          key={section.key}
          section={section}
          draft={draft}
          onChange={onChange}
          added={draft.addedFields[section.key] ?? []}
          onAdded={(fields) => setAdded(section.key, fields)}
        />
      ))}
      {draft.addedSections.map((section, si) => (
        <div
          key={section.uid}
          role="group"
          aria-label={`Your section ${si + 1}`}
          className="rounded-xl border border-border bg-foreground/[0.02] p-4"
        >
          <div className="flex items-start gap-2">
            <Labelled label="Section name" className="flex-1" messages={[]}>
              {(props) => (
                <Input
                  {...props}
                  value={section.label}
                  onChange={(e) =>
                    setSections(draft.addedSections.map((s, i) => (i === si ? { ...s, label: e.target.value } : s)))
                  }
                />
              )}
            </Labelled>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="mt-6"
              aria-label={`Remove ${section.label || `your section ${si + 1}`}`}
              onClick={() => setSections(draft.addedSections.filter((_, i) => i !== si))}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          </div>
          <OwnFields
            fields={section.fields}
            sectionName={section.label || `your section ${si + 1}`}
            canRemoveLast={false}
            onChange={(fields) =>
              setSections(draft.addedSections.map((s, i) => (i === si ? { ...s, fields } : s)))
            }
          />
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => setSections([...draft.addedSections, blankSection()])}>
        <Plus aria-hidden="true" />
        Add section
      </Button>
    </div>
  )
}

function BaseSection({
  section,
  draft,
  onChange,
  added,
  onAdded,
}: {
  section: PracticeSectionSpec
  draft: BasedDraft
  onChange: (draft: BasedDraft) => void
  added: AddedDraftField[]
  onAdded: (fields: AddedDraftField[]) => void
}) {
  const hidden = draft.hiddenSections.includes(section.key)
  return (
    <div role="group" aria-label={section.label} className="rounded-xl border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className={hidden ? "text-sm text-muted-foreground line-through" : "text-sm font-semibold text-foreground"}>
          {section.label}
        </h3>
        {canHideSection(draft.base, section) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`${hidden ? "Show" : "Hide"} the ${section.label} section`}
            onClick={() => onChange({ ...draft, hiddenSections: toggle(draft.hiddenSections, section.key) })}
          >
            {hidden ? "Show section" : "Hide section"}
          </Button>
        )}
      </div>
      {!hidden && (
        <>
          <ul className="mt-2 space-y-1">
            {section.fields.map((field) => {
              const path = `${section.key}.${field.key}`
              const fieldHidden = draft.hiddenFields.includes(path)
              return (
                <li key={field.key} className="flex items-start justify-between gap-3 text-muted-foreground">
                  <div className={fieldHidden ? "line-through" : undefined}>
                    <p className="text-[13px]">{field.label}</p>
                    {!fieldHidden &&
                      (field.source ? (
                        <FromTheChart />
                      ) : (
                        field.ai_hint && <p className="line-clamp-2 text-[12px]">{field.ai_hint}</p>
                      ))}
                  </div>
                  {canHide(draft.base, path) ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={`${fieldHidden ? "Show" : "Hide"} ${field.label}`}
                      onClick={() => onChange({ ...draft, hiddenFields: toggle(draft.hiddenFields, path) })}
                    >
                      {fieldHidden ? "Show" : "Hide"}
                    </Button>
                  ) : (
                    <span className="shrink-0 pt-1 text-[12px]">Always included</span>
                  )}
                </li>
              )
            })}
          </ul>
          <OwnFields
            fields={added}
            sectionName={section.label}
            canRemoveLast
            onChange={(fields) => onAdded(fields.map((f) => ({ after: null, ...f })))}
          />
        </>
      )}
    </div>
  )
}

/** Fields the practice adds: a name, a shape and what goes in each. */
function OwnFields<F extends DraftField>({
  fields,
  sectionName,
  canRemoveLast,
  onChange,
}: {
  fields: F[]
  sectionName: string
  canRemoveLast: boolean
  onChange: (fields: (F | DraftField)[]) => void
}) {
  const update = (index: number, patch: Partial<DraftField>) =>
    onChange(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)))

  return (
    <div className="mt-3 space-y-3">
      {fields.map((field, fi) => (
        <div key={field.uid} role="group" aria-label={`Your field ${fi + 1} in ${sectionName}`} className="space-y-2 border-l-2 border-secondary-300 pl-3">
          <div className="flex items-start gap-2">
            <Labelled label="Field name" className="flex-1" messages={[]}>
              {(props) => <Input {...props} autoFocus={!field.label} value={field.label} onChange={(e) => update(fi, { label: e.target.value })} />}
            </Labelled>
            <Labelled label="Shape" className="w-36" messages={[]}>
              {(props) => (
                <select
                  {...props}
                  value={field.kind}
                  onChange={(e) => update(fi, { kind: e.target.value as PracticeFieldKind })}
                  className={SELECT_CLASS}
                >
                  <option value="text">Paragraph</option>
                  <option value="list">List</option>
                  <option value="diagnoses">Diagnoses</option>
                </select>
              )}
            </Labelled>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="mt-6"
              aria-label={`Remove ${field.label || `your field ${fi + 1}`}`}
              disabled={!canRemoveLast && fields.length === 1}
              onClick={() => onChange(fields.filter((_, i) => i !== fi))}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          </div>
          <Labelled label="What goes here" messages={[]}>
            {(props) => (
              <Textarea {...props} rows={2} className="min-h-[56px]" value={field.ai_hint} onChange={(e) => update(fi, { ai_hint: e.target.value })} />
            )}
          </Labelled>
        </div>
      ))}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label={`Add a field to ${sectionName}`}
        onClick={() => onChange([...fields, blankField()])}
      >
        <Plus aria-hidden="true" />
        Add field
      </Button>
    </div>
  )
}
