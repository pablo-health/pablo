// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { ReactNode } from "react"
import { Plus, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { diagnosisText, type StatedDiagnosis } from "@/lib/statedDiagnoses"

/** Something to offer beside one stated diagnosis, such as adding it to the chart. */
export type DiagnosisAction = (dx: StatedDiagnosis) => ReactNode

export function DiagnosesList({
  items,
  action,
}: {
  items: StatedDiagnosis[]
  action?: DiagnosisAction
}) {
  return (
    <ul className="space-y-1">
      {items.map((dx, i) => (
        <li key={i} className="text-sm text-neutral-900 flex items-center gap-2">
          <span className="shrink-0">-</span>
          <span className="flex-1">{diagnosisText(dx)}</span>
          {action?.(dx)}
        </li>
      ))}
    </ul>
  )
}

const BLANK: StatedDiagnosis = { label: "", code: null, status: null }

/** One row per diagnosis: what it is, its code, and its status, each as written. */
export function DiagnosesEditor({
  label,
  items,
  onChange,
}: {
  label: string
  items: StatedDiagnosis[]
  onChange: (next: StatedDiagnosis[]) => void
}) {
  const update = (index: number, patch: Partial<StatedDiagnosis>) =>
    onChange(items.map((dx, i) => (i === index ? { ...dx, ...patch } : dx)))

  return (
    <div role="group" aria-label={label} className="space-y-2">
      {items.map((dx, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            aria-label={`Diagnosis ${i + 1}`}
            placeholder="Diagnosis"
            value={dx.label}
            onChange={(e) => update(i, { label: e.target.value })}
            className="flex-1"
          />
          <Input
            aria-label={`Code ${i + 1}`}
            placeholder="Code"
            value={dx.code ?? ""}
            onChange={(e) => update(i, { code: e.target.value || null })}
            className="w-28"
          />
          <Input
            aria-label={`Status ${i + 1}`}
            placeholder="Status, e.g. rule out"
            value={dx.status ?? ""}
            onChange={(e) => update(i, { status: e.target.value || null })}
            className="w-44"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove diagnosis ${i + 1}`}
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          >
            <X className="w-4 h-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...items, BLANK])}>
        <Plus className="w-4 h-4 mr-1" />
        Add a diagnosis
      </Button>
    </div>
  )
}
