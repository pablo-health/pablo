// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Who to call, and how.
 *
 * Three boxes rather than one. The chart reads this back as three fields,
 * and a single line would have to be picked apart by whoever is trying to
 * make the call. The answer is `{name, relationship, phone}`, which is what
 * the save route stores and all of what it asks for.
 *
 * **All three fields go on every change, including the empty ones.** A block
 * with a name and no number is a question still to finish, and saying so is
 * the save route's job: it names the field that is blank in the same words
 * as the label above it. Nothing here decides whether the answer will do,
 * for the same reason nothing else in this walk does.
 *
 * The question above the boxes is the practice's own wording, like every
 * other item they write themselves, which is why this file has no heading
 * in it and why an item stored without a label renders as a step still to
 * come.
 */

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  CONTACT_NAME_LABEL,
  CONTACT_PHONE_LABEL,
  CONTACT_RELATIONSHIP_LABEL,
} from "../formsCopy"
import { QuestionFrame, labelOf } from "./QuestionFrame"
import type { AnswerValue, ItemRenderer, ItemRendererProps } from "./types"

/** Matches `CONTACT_MAX_LEN` in `backend/app/intake/answers.py`. */
export const CONTACT_MAX = 200

/**
 * The three fields, in the order they are asked for.
 *
 * `type="tel"` on the number for the keypad it opens on a phone, which is
 * where most of these are typed. It is not a format: a phone number written
 * however somebody writes one is what the route accepts.
 */
const FIELDS = [
  {
    key: "name",
    label: CONTACT_NAME_LABEL,
    testId: "forms-contact-name",
    type: "text",
    autoComplete: "name",
  },
  {
    key: "relationship",
    label: CONTACT_RELATIONSHIP_LABEL,
    testId: "forms-contact-relationship",
    type: "text",
    autoComplete: "off",
  },
  {
    key: "phone",
    label: CONTACT_PHONE_LABEL,
    testId: "forms-contact-phone",
    type: "tel",
    autoComplete: "tel",
  },
] as const

function fieldIn(value: AnswerValue | null, key: string): string {
  const held = value?.[key]
  return typeof held === "string" ? held : ""
}

function EmergencyContactItem({ item, value, onChange }: ItemRendererProps) {
  const answerWith = (key: string, next: string): AnswerValue => ({
    name: fieldIn(value, "name"),
    relationship: fieldIn(value, "relationship"),
    phone: fieldIn(value, "phone"),
    [key]: next,
  })

  return (
    <QuestionFrame item={item}>
      {() => (
        <div className="space-y-4">
          {FIELDS.map((field) => {
            const inputId = `forms-contact-${field.key}-${item.id}`
            return (
              <div key={field.key} className="space-y-1.5">
                <Label htmlFor={inputId}>{field.label}</Label>
                <Input
                  id={inputId}
                  data-testid={field.testId}
                  type={field.type}
                  autoComplete={field.autoComplete}
                  maxLength={CONTACT_MAX}
                  value={fieldIn(value, field.key)}
                  onChange={(e) => onChange(answerWith(field.key, e.target.value))}
                />
              </div>
            )
          })}
        </div>
      )}
    </QuestionFrame>
  )
}

export const emergencyContactRenderer: ItemRenderer = {
  Component: EmergencyContactItem,
  answerable: true,
  label: labelOf,
  // What they typed, back to them. The review screen is where somebody
  // checks their own answer before sending it, and a contact they cannot
  // read back is one they cannot check.
  summary: (value) => {
    const parts = FIELDS.map((field) => fieldIn(value, field.key).trim()).filter(
      (part) => part !== "",
    )
    return parts.length === 0 ? null : parts.join(" · ")
  },
}
