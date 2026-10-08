// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One control per input a note type declares: a select for a choice, a text
 * box otherwise. Shared by every form that starts a note of a type with
 * inputs (an appointment, a transcript upload), so they ask for the same
 * values the same way. The form lays each one out (label, hint, error) and
 * styles its text boxes; a select keeps the shared select's look.
 */

"use client"

import { Fragment, type CSSProperties, type ReactNode } from "react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { NoteInputSchema } from "@/types/noteTypes"

export interface NoteInputFieldsProps {
  inputs: NoteInputSchema[]
  values: Record<string, string>
  onChange: (key: string, value: string) => void
  /** Lays out one input around its control. */
  renderField: (input: NoteInputSchema, control: ReactNode) => ReactNode
  textClassName?: string
  textStyle?: CSSProperties
  /** Inputs to mark invalid (a required one left empty). */
  invalidKeys?: ReadonlySet<string>
}

export function NoteInputFields({
  inputs,
  values,
  onChange,
  renderField,
  textClassName,
  textStyle,
  invalidKeys,
}: NoteInputFieldsProps) {
  return (
    <>
      {inputs.map((input) => {
        const invalid = invalidKeys?.has(input.key) || undefined
        const control =
          input.kind === "choice" ? (
            <Select value={values[input.key] ?? ""} onValueChange={(v) => onChange(input.key, v)}>
              <SelectTrigger
                aria-label={input.label}
                aria-required={input.required}
                aria-invalid={invalid}
                className="w-full"
              >
                <SelectValue placeholder="Choose…" />
              </SelectTrigger>
              <SelectContent>
                {input.options.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <input
              value={values[input.key] ?? ""}
              aria-label={input.label}
              aria-required={input.required}
              aria-invalid={invalid}
              onChange={(e) => onChange(input.key, e.target.value)}
              className={textClassName}
              style={textStyle}
            />
          )
        return <Fragment key={input.key}>{renderField(input, control)}</Fragment>
      })}
    </>
  )
}
