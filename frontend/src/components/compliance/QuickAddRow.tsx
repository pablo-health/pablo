// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useCreateComplianceItem } from "@/hooks/useCompliance"
import type { ComplianceTemplate } from "@/types/compliance"

interface QuickAddRowProps {
  templates: ComplianceTemplate[]
}

export function QuickAddRow({ templates }: QuickAddRowProps) {
  const [open, setOpen] = useState(false)
  const create = useCreateComplianceItem()

  const sorted = useMemo(
    () => [...templates].sort((a, b) => a.sort_order - b.sort_order),
    [templates],
  )

  const [itemType, setItemType] = useState<string>("")
  const [label, setLabel] = useState("")
  const [dueDate, setDueDate] = useState("")

  function reset() {
    setItemType("")
    setLabel("")
    setDueDate("")
    setOpen(false)
  }

  async function submit() {
    if (!itemType || !label) return
    await create.mutateAsync({
      item_type: itemType,
      label,
      due_date: dueDate || null,
      notes: null,
    })
    reset()
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full mt-3 rounded-lg border border-dashed border-neutral-300 px-3 py-2.5 text-sm text-neutral-600 hover:border-primary-300 hover:bg-primary-50/40 hover:text-primary-700 transition-colors flex items-center justify-center gap-2"
      >
        <span className="text-base leading-none">+</span>
        <span>Add a reminder</span>
      </button>
    )
  }

  return (
    <div className="mt-3 rounded-lg border border-primary-200 bg-primary-50/30 p-3 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="quick-add-type" className="text-xs">
            Type
          </Label>
          <Select value={itemType} onValueChange={(v) => {
            setItemType(v)
            const t = sorted.find((x) => x.item_type === v)
            if (t && !label) setLabel(t.label)
          }}>
            <SelectTrigger id="quick-add-type" className="h-9">
              <SelectValue placeholder="Pick a type" />
            </SelectTrigger>
            <SelectContent>
              {sorted.map((t) => (
                <SelectItem key={t.item_type} value={t.item_type}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="quick-add-due" className="text-xs">
            Due date
          </Label>
          <Input
            id="quick-add-due"
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
            className="h-9"
          />
        </div>
      </div>
      <div>
        <Label htmlFor="quick-add-label" className="text-xs">
          Label
        </Label>
        <Input
          id="quick-add-label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. NY LMHC renewal"
          className="h-9"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={reset}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={submit}
          disabled={!itemType || !label || create.isPending}
        >
          {create.isPending ? "Adding…" : "Add reminder"}
        </Button>
      </div>
    </div>
  )
}
