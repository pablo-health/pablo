// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus, Sun, Moon, Sparkles, LayoutGrid } from "lucide-react"
import type { AppointmentStatus } from "@/types/scheduling"
import { EditorialMiniMonth } from "./EditorialMiniMonth"

export type EditorialTheme = "light" | "dark"
export type CalendarStyle = "editorial" | "classic"

const STATUS_OPTIONS: { value: AppointmentStatus; label: string }[] = [
  { value: "confirmed", label: "Confirmed" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "no_show", label: "No-shows" },
]

interface EditorialSidebarProps {
  selected: Date
  statusFilters: Set<AppointmentStatus>
  theme: EditorialTheme
  style: CalendarStyle
  onSelectDate: (date: Date) => void
  onCreateNew: () => void
  onToggleStatus: (status: AppointmentStatus) => void
  onThemeChange: (theme: EditorialTheme) => void
  onStyleChange: (style: CalendarStyle) => void
}

export function EditorialSidebar({
  selected,
  statusFilters,
  theme,
  style,
  onSelectDate,
  onCreateNew,
  onToggleStatus,
  onThemeChange,
  onStyleChange,
}: EditorialSidebarProps) {
  return (
    <aside
      className="hidden w-[280px] shrink-0 flex-col gap-6 px-5 py-6 lg:flex"
      style={{
        backgroundColor: "var(--ed-rail)",
        borderRight: "1px solid var(--ed-hairline)",
        color: "var(--ed-ink)",
      }}
    >
      <EditorialMiniMonth selected={selected} onSelect={onSelectDate} />

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

      <Divider />

      <Section title="Show on calendar">
        <div className="flex flex-col gap-2.5">
          {STATUS_OPTIONS.map((opt) => {
            const checked = statusFilters.has(opt.value)
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
                <span style={{ color: "var(--ed-ink)" }}>{opt.label}</span>
              </label>
            )
          })}
        </div>
      </Section>

      <Divider />

      <Section title="Appearance">
        <div className="flex flex-col gap-3">
          <SegmentedToggle<EditorialTheme>
            value={theme}
            onChange={onThemeChange}
            options={[
              { value: "light", label: "Light", icon: Sun },
              { value: "dark", label: "Dark", icon: Moon },
            ]}
          />
          <SegmentedToggle<CalendarStyle>
            value={style}
            onChange={onStyleChange}
            options={[
              { value: "editorial", label: "Editorial", icon: Sparkles },
              { value: "classic", label: "Classic", icon: LayoutGrid },
            ]}
          />
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

interface SegmentedToggleProps<T extends string> {
  value: T
  onChange: (next: T) => void
  options: {
    value: T
    label: string
    icon: React.ComponentType<{ className?: string }>
  }[]
}

function SegmentedToggle<T extends string>({
  value,
  onChange,
  options,
}: SegmentedToggleProps<T>) {
  return (
    <div
      className="flex w-full rounded-full p-0.5"
      style={{ backgroundColor: "var(--ed-canvas-elev)" }}
    >
      {options.map((opt) => {
        const active = opt.value === value
        const Icon = opt.icon
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-all"
            style={{
              backgroundColor: active ? "var(--ed-ink)" : "transparent",
              color: active ? "var(--ed-cta-fg)" : "var(--ed-ink-muted)",
            }}
            aria-pressed={active}
          >
            <Icon className="h-3.5 w-3.5" />
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
