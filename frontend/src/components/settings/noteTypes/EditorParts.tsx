// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ArrowDown, ArrowUp, Trash2 } from "lucide-react"
import { useId, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export const SELECT_CLASS = cn(
  "border-input h-9 w-full rounded-md border bg-transparent px-3 py-1 text-sm shadow-xs outline-none",
  "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
)

/** The server's messages for one control, shown right under it. */
export function FieldMessages({ id, messages }: { id?: string; messages: string[] }) {
  if (messages.length === 0) return null
  return (
    <div id={id} className="mt-1 space-y-0.5">
      {messages.map((message) => (
        <p key={message} className="text-[12px] leading-snug text-red-700">
          {message}
        </p>
      ))}
    </div>
  )
}

/**
 * A labelled control with its messages. `children` gets the props that tie
 * the control to its label and its messages.
 */
export function Labelled({
  label,
  hint,
  messages,
  children,
  className,
}: {
  label: string
  hint?: string
  messages: string[]
  children: (props: { id: string; "aria-invalid"?: true; "aria-describedby"?: string }) => ReactNode
  className?: string
}) {
  const id = useId()
  const messagesId = `${id}-messages`
  const invalid = messages.length > 0
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-[12.5px] font-semibold text-foreground">
        {label}
        {hint && <span className="ml-1.5 font-normal text-muted-foreground">{hint}</span>}
      </label>
      {children({ id, ...(invalid ? { "aria-invalid": true, "aria-describedby": messagesId } : {}) })}
      <FieldMessages id={messagesId} messages={messages} />
    </div>
  )
}

/** Sources printed from the values entered for the visit rather than from the chart. */
const VISIT_SOURCES = new Set(["place_of_service"])

/**
 * In place of a field's shape and "What goes here": the field names a source,
 * so code prints it and nothing about it is drafted. Its shape is fixed by
 * that source, so neither is offered. The label says where the text comes from.
 */
export function FieldSource({ source }: { source: string }) {
  return (
    <p className="text-[12.5px] text-muted-foreground">
      {VISIT_SOURCES.has(source) ? "From the visit" : "From the chart"}
    </p>
  )
}

/** Move up, move down and remove, for one row of a list. */
export function RowActions({
  name,
  index,
  count,
  onMove,
  onRemove,
  canRemove = true,
}: {
  name: string
  index: number
  count: number
  onMove: (delta: number) => void
  onRemove: () => void
  canRemove?: boolean
}) {
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Button type="button" variant="ghost" size="icon-sm" aria-label={`Move ${name} up`} disabled={index === 0} onClick={() => onMove(-1)}>
        <ArrowUp aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={`Move ${name} down`}
        disabled={index === count - 1}
        onClick={() => onMove(1)}
      >
        <ArrowDown aria-hidden="true" />
      </Button>
      <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove ${name}`} disabled={!canRemove} onClick={onRemove}>
        <Trash2 aria-hidden="true" />
      </Button>
    </div>
  )
}
