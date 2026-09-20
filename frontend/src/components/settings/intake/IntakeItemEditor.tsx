// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ChevronDown, ChevronUp, Plus, X } from "lucide-react"
import { useId, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Toggle } from "@/components/settings/ui"
import {
  DISPLAY_ONLY_ITEM_TYPES,
  ITEM_TYPES,
  type IntakeItemInput,
  type IntakeVersionDetail,
  type ItemType,
} from "@/types/intakePackets"
import {
  ADD_QUESTION,
  ITEM_TYPE_HINTS,
  ITEM_TYPE_LABELS,
  NO_QUESTIONS,
  PUBLISHED_NOTICE,
  PUBLISH_BUTTON,
} from "./intakeCopy"
import { ItemConfigForm, type PublishedDocument } from "./ItemConfigForm"

interface IntakeItemEditorProps {
  version: IntakeVersionDetail
  onSave: (items: IntakeItemInput[]) => void
  onPublish: () => void
  saving?: boolean
  publishing?: boolean
  /** What the server said when it refused to publish, shown as it said it. */
  publishError?: string | null
  /** The practice's published documents, for a consent item to point at. */
  documents?: PublishedDocument[]
}

function nextKey(label: string, taken: string[]): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
  const stem = base && /^[a-z]/.test(base) ? base.slice(0, 60) : "question"
  if (!taken.includes(stem)) return stem
  let n = 2
  while (taken.includes(`${stem}_${n}`)) n += 1
  return `${stem}_${n}`
}

function toInput(items: IntakeVersionDetail["items"]): IntakeItemInput[] {
  return items.map((item) => ({
    key: item.key,
    item_type: item.item_type,
    required: item.required,
    resign_on_new_version: item.resign_on_new_version,
    config: item.config,
  }))
}

/**
 * The ordered list of questions on one draft version.
 *
 * Held in local state and saved on demand rather than on every keystroke: a
 * practice building a form moves items around and fills settings in out of
 * order, and a save per change would be a request per character.
 *
 * A published version renders read-only with one line saying what to do
 * instead. The server refuses the edit either way; this is so a practice does
 * not type into a box whose contents can never be saved.
 */
export function IntakeItemEditor({
  version,
  onSave,
  onPublish,
  saving,
  publishing,
  publishError,
  documents,
}: IntakeItemEditorProps) {
  const [items, setItems] = useState<IntakeItemInput[]>(() => toInput(version.items))
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const [addType, setAddType] = useState<ItemType>("free_text")
  const [shownVersionId, setShownVersionId] = useState(version.id)
  const editorId = useId()
  const published = version.published_at !== null

  // A different version is a different list, so switching between two of them
  // starts over rather than leaving the previous one's questions on screen
  // under the new one's header. Adjusted during render rather than in an
  // effect, which is React's own answer for state derived from a prop.
  //
  // Keyed on the version id and not on the items, deliberately: a refetch that
  // lands while a practice is editing must not throw away what they typed.
  if (shownVersionId !== version.id) {
    setShownVersionId(version.id)
    setItems(toInput(version.items))
    setOpenIndex(null)
  }

  function patch(index: number, changes: Partial<IntakeItemInput>) {
    setItems((current) => current.map((item, i) => (i === index ? { ...item, ...changes } : item)))
  }

  function move(index: number, delta: number) {
    const target = index + delta
    if (target < 0 || target >= items.length) return
    setItems((current) => {
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
    setOpenIndex(openIndex === index ? target : openIndex)
  }

  function remove(index: number) {
    setItems((current) => current.filter((_, i) => i !== index))
    setOpenIndex(null)
  }

  function add() {
    const key = nextKey(ITEM_TYPE_LABELS[addType] ?? addType, items.map((i) => i.key))
    setItems((current) => [
      ...current,
      {
        key,
        item_type: addType,
        required: !DISPLAY_ONLY_ITEM_TYPES.includes(addType),
        resign_on_new_version: false,
        config: {},
      },
    ])
    setOpenIndex(items.length)
  }

  if (published) {
    return (
      <div className="space-y-3">
        <p className="text-[13px] text-muted-foreground">{PUBLISHED_NOTICE}</p>
        <ol className="space-y-1">
          {version.items.map((item) => (
            <li key={item.id} className="text-sm text-foreground">
              {ITEM_TYPE_LABELS[item.item_type] ?? item.item_type}
              <span className="ml-2 text-[12.5px] text-muted-foreground">{item.key}</span>
            </li>
          ))}
        </ol>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {items.length === 0 && <p className="text-[13px] text-muted-foreground">{NO_QUESTIONS}</p>}

      <ol className="space-y-2">
        {items.map((item, index) => {
          const open = openIndex === index
          const displayOnly = DISPLAY_ONLY_ITEM_TYPES.includes(item.item_type)
          return (
            <li key={item.key} className="rounded-xl border border-border p-3">
              <div className="flex items-center justify-between gap-2">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setOpenIndex(open ? null : index)}
                  aria-expanded={open}
                >
                  <div className="text-sm font-semibold text-foreground">
                    {ITEM_TYPE_LABELS[item.item_type] ?? item.item_type}
                  </div>
                  <div className="mt-0.5 text-[12.5px] text-muted-foreground">{item.key}</div>
                </button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${item.key} up`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ChevronUp className="h-4 w-4" aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${item.key} down`}
                  disabled={index === items.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ChevronDown className="h-4 w-4" aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove ${item.key}`}
                  onClick={() => remove(index)}
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>

              {open && (
                <div className="mt-3 space-y-3 border-t border-border pt-3">
                  <p className="text-[12.5px] text-muted-foreground">
                    {ITEM_TYPE_HINTS[item.item_type]}
                  </p>
                  <div>
                    <Label htmlFor={`${editorId}-${index}-key`}>Name</Label>
                    <Input
                      id={`${editorId}-${index}-key`}
                      value={item.key}
                      onChange={(e) => patch(index, { key: e.target.value })}
                    />
                  </div>
                  <ItemConfigForm
                    itemType={item.item_type}
                    config={item.config}
                    onChange={(config) => patch(index, { config })}
                    idPrefix={`${editorId}-${index}`}
                    documents={documents}
                  />
                  {!displayOnly && (
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-foreground">They have to answer this</span>
                      <Toggle
                        checked={item.required}
                        onChange={(required) => patch(index, { required })}
                        label={`${item.key} has to be answered`}
                      />
                    </div>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>

      <div className="flex items-center gap-2">
        <Select value={addType} onValueChange={(value) => setAddType(value as ItemType)}>
          <SelectTrigger className="w-56" aria-label="Kind of question">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ITEM_TYPES.map((type) => (
              <SelectItem key={type} value={type}>
                {ITEM_TYPE_LABELS[type] ?? type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          {ADD_QUESTION}
        </Button>
      </div>

      {publishError && (
        <p role="alert" className="text-[13px] text-destructive">
          {publishError}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button type="button" onClick={() => onSave(items)} disabled={saving}>
          Save
        </Button>
        <Button type="button" variant="outline" onClick={onPublish} disabled={publishing}>
          {PUBLISH_BUTTON}
        </Button>
      </div>
    </div>
  )
}
