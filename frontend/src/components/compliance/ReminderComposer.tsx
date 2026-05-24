// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateComplianceItem,
  useUpdateComplianceItem,
} from "@/hooks/useCompliance"
import type {
  ComplianceItem,
  ComplianceTemplate,
} from "@/types/compliance"
import { CATEGORIES, categoryFor, type CategoryId } from "./categories"

type ComposerView =
  | { mode: "browse" }
  | {
      mode: "edit"
      template: ComplianceTemplate
      existing: ComplianceItem | null
    }

export interface ReminderComposerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  templates: ComplianceTemplate[]
  items: ComplianceItem[]
  /** When provided, the composer opens directly into edit mode for this item. */
  initialItem?: ComplianceItem | null
}

export function ReminderComposer({
  open,
  onOpenChange,
  templates,
  items,
  initialItem,
}: ReminderComposerProps) {
  // Body is keyed on `open` so a second open of the dialog gets a fresh
  // mount — internal view/edit state resets without a self-resetting effect.
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <ComposerBody
          key={String(open)}
          templates={templates}
          items={items}
          initialItem={initialItem}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

interface ComposerBodyProps {
  templates: ComplianceTemplate[]
  items: ComplianceItem[]
  initialItem?: ComplianceItem | null
  onClose: () => void
}

function ComposerBody({
  templates,
  items,
  initialItem,
  onClose,
}: ComposerBodyProps) {
  const templateByType = useMemo(() => {
    const m = new Map<string, ComplianceTemplate>()
    for (const t of templates) m.set(t.item_type, t)
    return m
  }, [templates])

  const initialView: ComposerView = useMemo(() => {
    if (initialItem) {
      const t = templateByType.get(initialItem.item_type)
      if (t) return { mode: "edit", template: t, existing: initialItem }
    }
    return { mode: "browse" }
  }, [initialItem, templateByType])

  const [view, setView] = useState<ComposerView>(initialView)
  // Bumped on every transition into edit mode so EditView remounts and
  // re-initializes its form state from props. Without this, "Save & add
  // another" reuses the prior instance's input values.
  const [editEpoch, setEditEpoch] = useState(0)

  return (
    <>
      {view.mode === "browse" ? (
          <BrowseView
            templates={templates}
            items={items}
            onPickTemplate={(t) => {
              setView({
                mode: "edit",
                template: t,
                existing: t.multi_instance
                  ? null
                  : (items.find((i) => i.item_type === t.item_type) ?? null),
              })
              setEditEpoch((n) => n + 1)
            }}
            onPickExisting={(i) => {
              const t = templateByType.get(i.item_type)
              if (!t) return
              setView({ mode: "edit", template: t, existing: i })
              setEditEpoch((n) => n + 1)
            }}
            onClose={onClose}
          />
        ) : (
          <EditView
            key={editEpoch}
            template={view.template}
            existing={view.existing}
            onBack={() => setView({ mode: "browse" })}
            onSaved={(stayOnTemplate) => {
              if (stayOnTemplate && view.template.multi_instance) {
                setView({
                  mode: "edit",
                  template: view.template,
                  existing: null,
                })
                setEditEpoch((n) => n + 1)
              } else {
                setView({ mode: "browse" })
              }
            }}
            onClose={onClose}
          />
        )}
    </>
  )
}

interface BrowseViewProps {
  templates: ComplianceTemplate[]
  items: ComplianceItem[]
  onPickTemplate: (t: ComplianceTemplate) => void
  onPickExisting: (i: ComplianceItem) => void
  onClose: () => void
}

function BrowseView({
  templates,
  items,
  onPickTemplate,
  onPickExisting,
  onClose,
}: BrowseViewProps) {
  const byCategory = useMemo(() => {
    const map = new Map<CategoryId, ComplianceTemplate[]>()
    const ordered = [...templates].sort((a, b) => a.sort_order - b.sort_order)
    for (const t of ordered) {
      const c = categoryFor(t.item_type)
      const arr = map.get(c) ?? []
      arr.push(t)
      map.set(c, arr)
    }
    return map
  }, [templates])

  const itemsByType = useMemo(() => {
    const m = new Map<string, ComplianceItem[]>()
    for (const i of items) {
      const arr = m.get(i.item_type) ?? []
      arr.push(i)
      m.set(i.item_type, arr)
    }
    return m
  }, [items])

  const hasAny = items.length > 0

  return (
    <>
      <DialogHeader>
        <DialogTitle className="font-display">
          {hasAny ? "Add or edit a reminder" : "Pick what to track"}
        </DialogTitle>
        <DialogDescription>
          {hasAny
            ? "Tap a card to add a new reminder, or tap a tracked item to edit it."
            : "Tap any card to set a date. Start with one or two — you can always add more."}
        </DialogDescription>
      </DialogHeader>

      <div
        className="max-h-[60vh] overflow-y-auto pr-1 -mr-1 space-y-5 pt-2"
        data-testid="composer-browse"
      >
        {CATEGORIES.map((cat) => {
          const cards = byCategory.get(cat.id)
          if (!cards || cards.length === 0) return null
          return (
            <section key={cat.id} aria-label={cat.label}>
              <header className="flex items-baseline gap-2 mb-2">
                <h3 className="text-xs uppercase tracking-wide text-neutral-500 font-medium">
                  {cat.label}
                </h3>
                <span className="text-[11px] text-neutral-400">
                  {cat.hint}
                </span>
              </header>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {cards.map((t) => (
                  <TemplateCard
                    key={t.item_type}
                    template={t}
                    trackedItems={itemsByType.get(t.item_type) ?? []}
                    onPickTemplate={() => onPickTemplate(t)}
                    onPickExisting={onPickExisting}
                  />
                ))}
              </div>
            </section>
          )
        })}
      </div>

      <div className="flex justify-end pt-3 border-t border-neutral-100 mt-2">
        <Button variant="ghost" onClick={onClose}>
          Done
        </Button>
      </div>
    </>
  )
}

interface TemplateCardProps {
  template: ComplianceTemplate
  trackedItems: ComplianceItem[]
  onPickTemplate: () => void
  onPickExisting: (i: ComplianceItem) => void
}

function TemplateCard({
  template,
  trackedItems,
  onPickTemplate,
  onPickExisting,
}: TemplateCardProps) {
  const isTracked = trackedItems.length > 0
  const isMulti = template.multi_instance

  return (
    <div
      className={`group rounded-xl border p-3 transition-colors ${
        isTracked
          ? "border-emerald-200 bg-emerald-50/30"
          : "border-neutral-200 bg-white hover:border-primary-300 hover:bg-primary-50/30"
      }`}
    >
      <button
        type="button"
        onClick={() => {
          if (!isMulti && isTracked) {
            onPickExisting(trackedItems[0])
          } else {
            onPickTemplate()
          }
        }}
        className="w-full text-left"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-neutral-900 leading-tight">
              {template.label}
            </p>
            <p className="text-xs text-neutral-500 mt-1 line-clamp-2">
              {template.description}
            </p>
          </div>
          <StatusBadge
            isTracked={isTracked}
            isMulti={isMulti}
            count={trackedItems.length}
          />
        </div>
      </button>

      {isMulti && trackedItems.length > 0 && (
        <ul
          className="mt-3 pt-3 border-t border-emerald-100 space-y-1"
          role="list"
        >
          {trackedItems.map((i) => (
            <li key={i.id}>
              <button
                type="button"
                onClick={() => onPickExisting(i)}
                className="w-full text-left text-xs px-2 py-1 rounded hover:bg-white text-neutral-700 truncate flex items-center justify-between gap-2"
              >
                <span className="truncate">{i.label}</span>
                <span className="text-neutral-400 text-[10px] shrink-0">
                  Edit ›
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function StatusBadge({
  isTracked,
  isMulti,
  count,
}: {
  isTracked: boolean
  isMulti: boolean
  count: number
}) {
  if (!isTracked) {
    return (
      <span className="text-[10px] uppercase tracking-wide text-neutral-400 shrink-0 mt-0.5">
        Add
      </span>
    )
  }
  if (isMulti) {
    return (
      <span className="text-[10px] uppercase tracking-wide text-emerald-700 bg-emerald-100 rounded-full px-2 py-0.5 shrink-0">
        {count} tracked
      </span>
    )
  }
  return (
    <span className="text-[10px] uppercase tracking-wide text-emerald-700 bg-emerald-100 rounded-full px-2 py-0.5 shrink-0">
      Tracked
    </span>
  )
}

interface EditViewProps {
  template: ComplianceTemplate
  existing: ComplianceItem | null
  onBack: () => void
  onSaved: (stayOnTemplate: boolean) => void
  onClose: () => void
}

function EditView({
  template,
  existing,
  onBack,
  onSaved,
  onClose,
}: EditViewProps) {
  const [dueDate, setDueDate] = useState(existing?.due_date ?? "")
  const [notes, setNotes] = useState(existing?.notes ?? "")
  const [label, setLabel] = useState(
    existing?.label ?? (template.multi_instance ? "" : template.label),
  )

  const createItem = useCreateComplianceItem()
  const updateItem = useUpdateComplianceItem()

  const isSaving = createItem.isPending || updateItem.isPending
  const labelOk = template.multi_instance
    ? label.trim().length > 0
    : true
  const canSave = labelOk && (dueDate.length > 0 || notes.length > 0)

  async function save(addAnother: boolean) {
    const payload = {
      item_type: template.item_type,
      label: template.multi_instance ? label.trim() : template.label,
      due_date: dueDate || null,
      notes: notes || null,
    }
    if (existing) {
      await updateItem.mutateAsync({ id: existing.id, payload })
    } else {
      await createItem.mutateAsync(payload)
    }
    onSaved(addAnother)
  }

  return (
    <>
      <DialogHeader>
        <button
          type="button"
          onClick={onBack}
          className="self-start text-xs text-neutral-500 hover:text-neutral-800 transition-colors -mb-1"
        >
          ‹ All reminders
        </button>
        <DialogTitle className="font-display">
          {existing ? `Edit · ${existing.label}` : template.label}
        </DialogTitle>
        <DialogDescription>{template.description}</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 pt-2">
        {template.multi_instance && (
          <div className="space-y-1">
            <Label htmlFor="composer-label">Label</Label>
            <Input
              id="composer-label"
              placeholder={`e.g. ${template.label} — vendor or state`}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              autoFocus
            />
            <p className="text-[11px] text-neutral-500">
              Give it a name you&apos;ll recognize in your list.
            </p>
          </div>
        )}

        <div className="space-y-1">
          <Label htmlFor="composer-due">
            {template.cadence_days
              ? "Last completed / next due"
              : "Expiration date"}
          </Label>
          <Input
            id="composer-due"
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
          />
          {template.cadence_days != null && (
            <p className="text-[11px] text-neutral-500">
              Renews every {template.cadence_days} days.
            </p>
          )}
        </div>

        <div className="space-y-1">
          <Label htmlFor="composer-notes">Notes (optional)</Label>
          <Textarea
            id="composer-notes"
            placeholder="License number, carrier, NPI, etc."
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
          />
        </div>
      </div>

      <div className="flex flex-wrap justify-between gap-2 pt-4 border-t border-neutral-100 mt-2">
        <Button variant="ghost" onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <div className="flex gap-2">
          {template.multi_instance && !existing && (
            <Button
              variant="outline"
              onClick={() => save(true)}
              disabled={!canSave || isSaving}
            >
              Save &amp; add another
            </Button>
          )}
          <Button onClick={() => save(false)} disabled={!canSave || isSaving}>
            {isSaving ? "Saving…" : existing ? "Save changes" : "Save reminder"}
          </Button>
        </div>
      </div>
    </>
  )
}
