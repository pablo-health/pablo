// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { TASK_CATEGORIES, type TaskCategory } from "@/types/tasks"

export type CategorySelection = TaskCategory | "all"

interface CategoryFilterProps {
  selected: CategorySelection
  onSelect: (selection: CategorySelection) => void
  /** How many tasks each category holds; a category shows its count beside its name. */
  counts: Record<TaskCategory, number>
}

/** Which kind of task the panel shows. Compliance first, then the rest, then everything. */
export function CategoryFilter({ selected, onSelect, counts }: CategoryFilterProps) {
  const options: { id: CategorySelection; label: string }[] = [
    ...TASK_CATEGORIES.map((c) => ({ id: c.id, label: `${c.label} · ${counts[c.id]}` })),
    { id: "all", label: "All" },
  ]
  return (
    <div role="radiogroup" aria-label="Show tasks" className="flex flex-wrap gap-1.5 mb-3">
      {options.map((option) => {
        const active = option.id === selected
        return (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={active}
            data-testid={`task-category-${option.id}`}
            onClick={() => onSelect(option.id)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? "border-primary-300 bg-primary-50 text-primary-800"
                : "border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300"
            }`}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
