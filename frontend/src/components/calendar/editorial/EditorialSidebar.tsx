// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus } from "lucide-react"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import type { AppointmentStatus } from "@/types/scheduling"
import { EditorialMiniMonth } from "./EditorialMiniMonth"
import { editorialStatusMeta } from "./status"

// "light" tracks any light Pablo theme (via global tokens); "dark" = Midnight.
export type EditorialTheme = "light" | "dark"

const STATUS_OPTIONS: { value: AppointmentStatus; label: string }[] = [
  { value: "confirmed", label: "Confirmed" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "no_show", label: "No-shows" },
]

interface EditorialSidebarProps {
  selected: Date
  statusFilters: Set<AppointmentStatus>
  onSelectDate: (date: Date) => void
  onCreateNew: () => void
  onToggleStatus: (status: AppointmentStatus) => void
}

export function EditorialSidebar({
  selected,
  statusFilters,
  onSelectDate,
  onCreateNew,
  onToggleStatus,
}: EditorialSidebarProps) {
  const { readOnly } = useReadOnlyMode()

  return (
    <aside
      className="hidden w-[280px] shrink-0 flex-col gap-6 px-5 py-6 lg:flex"
      style={{
        backgroundColor: "var(--ed-rail)",
        borderRight: "1px solid var(--ed-hairline)",
        color: "var(--ed-ink)",
      }}
    >
      {!readOnly && (
        <button
          type="button"
          onClick={onCreateNew}
          className="flex w-full items-center justify-center gap-2 rounded-full px-5 py-3 text-sm font-semibold tracking-wide transition-all hover:translate-y-[-1px] active:translate-y-0"
          style={{
            backgroundColor: "var(--ed-cta-bg)",
            color: "var(--ed-cta-fg)",
            boxShadow: "var(--ed-shadow-card)",
          }}
        >
          <Plus className="h-4 w-4" />
          New appointment
        </button>
      )}

      <EditorialMiniMonth selected={selected} onSelect={onSelectDate} />

      <Divider />

      <Section title="Show on calendar">
        <div className="flex flex-col gap-2.5">
          {STATUS_OPTIONS.map((opt) => {
            const checked = statusFilters.has(opt.value)
            const rail = editorialStatusMeta(opt.value).rail
            return (
              <label
                key={opt.value}
                className="group flex cursor-pointer items-center gap-2.5 text-sm"
              >
                <span
                  className="flex h-4 w-4 items-center justify-center rounded-[4px] border transition-colors"
                  style={{
                    borderColor: checked ? "var(--ed-ink)" : "var(--ed-hairline-strong)",
                    backgroundColor: checked ? "var(--ed-ink)" : "transparent",
                  }}
                >
                  {checked && (
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 10 10"
                      fill="none"
                      aria-hidden
                    >
                      <path
                        d="M2 5L4 7L8 3"
                        stroke="var(--ed-cta-fg)"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </span>
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={checked}
                  onChange={() => onToggleStatus(opt.value)}
                />
                <span
                  aria-hidden
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: rail }}
                />
                <span style={{ color: "var(--ed-ink)" }}>{opt.label}</span>
              </label>
            )
          })}
        </div>
      </Section>
    </aside>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <h4
        className="text-[10px] font-semibold uppercase tracking-[0.2em]"
        style={{ color: "var(--ed-ink-soft)" }}
      >
        {title}
      </h4>
      {children}
    </div>
  )
}

function Divider() {
  return (
    <div className="h-px w-full" style={{ backgroundColor: "var(--ed-hairline)" }} />
  )
}
