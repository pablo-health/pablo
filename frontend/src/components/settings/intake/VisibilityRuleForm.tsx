// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * "Show only when" — asking a question only of the people it applies to.
 *
 * Three controls, in the order a therapist thinks: which earlier question,
 * what about its answer, and what to compare it to. The middle one is
 * built from the earlier question's own type, so a measure offers a score
 * and a tick-list offers its answers — there is no free-text operator box
 * and no way to compare a date against a total.
 *
 * **Only questions above this one are offered.** A rule may look backwards
 * and nowhere else, and the server refuses a version that breaks that. This
 * is where that stops being a refusal a practice has to discover: the list
 * holds what is already above, so the rule that cannot be published cannot
 * be written either.
 *
 * Nothing here decides whether a rule is valid. A draft may hold a
 * half-written one — a target picked and no condition yet — the same way it
 * may hold a half-written question, and publishing is where the server has
 * its say. What this does do is keep the half-written state expressible,
 * because an editor that refused it would be an editor that could not save
 * its own work in progress.
 */

import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { VisibleWhen } from "@/lib/intake/visibility"
import type { ChoiceOption, ItemConfig, ItemType } from "@/types/intakePackets"
import {
  ITEM_TYPE_LABELS,
  VISIBILITY_ALWAYS,
  VISIBILITY_CONDITION_LABEL,
  VISIBILITY_ITEM_NUMBER_LABEL,
  VISIBILITY_NOTHING_EARLIER,
  VISIBILITY_QUESTION_LABEL,
  VISIBILITY_TITLE,
  VISIBILITY_VALUE_LABEL,
} from "./intakeCopy"

/** One question a rule may point at: earlier on the form, and answerable. */
export interface VisibilityTarget {
  key: string
  item_type: ItemType
  label: string | null
  config: ItemConfig
}

/**
 * Question types nothing can be based on.
 *
 * A heading and a paragraph collect nothing, so there is no answer for a
 * condition to be about — the server says the same and refuses the publish.
 * The two file uploads are here for a different reason: nothing stores a
 * file yet, so a rule based on one could never hold, and offering it would
 * be offering a branch that quietly never fires.
 */
const CANNOT_BE_BRANCHED_ON: readonly ItemType[] = [
  "section",
  "instructions",
  "insurance_card",
  "document_request",
]

/** Whether *item* is a question a later one may be shown because of. */
export function canBeBranchedOn(itemType: ItemType): boolean {
  return !CANNOT_BE_BRANCHED_ON.includes(itemType)
}

interface VisibilityRuleFormProps {
  rule: VisibleWhen | null
  /** Every question above this one that can be branched on, in form order. */
  earlier: VisibilityTarget[]
  onChange: (rule: VisibleWhen | null) => void
  /** Prefix for the generated field ids, so two open items do not collide. */
  idPrefix: string
}

/** The sentinel the "no rule" row carries, since a Select needs a value. */
const NO_TARGET = "__always__"

/**
 * What a condition asks for beyond itself.
 *
 * `none` is the whole condition; the rest each render one more control.
 * `option` is a choice off the earlier question, `item` is which item of a
 * measure, and the numeric and date kinds are typed in.
 */
type Needs = "none" | "option" | "number" | "date" | "item"

interface Condition {
  /** The id this condition is picked by, and what a stored rule maps back to. */
  id: string
  label: string
  op: VisibleWhen["op"]
  needs: Needs
  /** Set for the conditions whose value is fixed by the condition itself. */
  value?: unknown
}

const ANSWERED: Condition = {
  id: "answered",
  label: "answered it at all",
  op: "answered",
  needs: "none",
}

/** The conditions a question of each type offers. */
function conditionsFor(target: VisibilityTarget): Condition[] {
  switch (target.item_type) {
    case "single_choice":
    case "multi_choice":
      return [
        { id: "eq_option", label: "picked", op: "eq", needs: "option" },
        { id: "neq_option", label: "did not pick", op: "neq", needs: "option" },
        ANSWERED,
      ]
    case "yes_no":
      return [
        { id: "yes", label: "said yes", op: "eq", needs: "none", value: true },
        { id: "no", label: "said no", op: "eq", needs: "none", value: false },
        ANSWERED,
      ]
    case "number":
    case "scale":
      return [
        { id: "gte_number", label: "answered at least", op: "gte", needs: "number" },
        { id: "lte_number", label: "answered at most", op: "lte", needs: "number" },
        ANSWERED,
      ]
    case "date":
      return [
        { id: "gte_date", label: "gave a date on or after", op: "gte", needs: "date" },
        { id: "lte_date", label: "gave a date on or before", op: "lte", needs: "date" },
        ANSWERED,
      ]
    case "instrument":
      return [
        { id: "score_gte", label: "scored at least", op: "score_gte", needs: "number" },
        { id: "score_lte", label: "scored at most", op: "score_lte", needs: "number" },
        { id: "item_gte", label: "answered one question at least", op: "item_gte", needs: "item" },
        ANSWERED,
      ]
    default:
      // A written answer, a name and date of birth, a contact block. There
      // is nothing about these a picker could offer a therapist that is not
      // a free-text comparison against somebody's own words.
      return [ANSWERED]
  }
}

/** Which condition a stored rule is, so the picker reads its own output back. */
function conditionOf(rule: VisibleWhen, offered: Condition[]): Condition | null {
  const matching = offered.filter((condition) => condition.op === rule.op)
  if (matching.length === 0) return null
  const exact = matching.find(
    (condition) => condition.value !== undefined && condition.value === rule.value,
  )
  return exact ?? matching.find((condition) => condition.value === undefined) ?? matching[0]
}

function optionsOf(config: ItemConfig): ChoiceOption[] {
  const options = config.options
  return Array.isArray(options) ? (options as ChoiceOption[]) : []
}

/** What an earlier question is called in the picker. */
function headingOf(target: VisibilityTarget): string {
  const label = target.label?.trim()
  return label ? label : (ITEM_TYPE_LABELS[target.item_type] ?? target.item_type)
}

function numberOrUndefined(raw: string): number | undefined {
  return raw === "" ? undefined : Number(raw)
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {}
}

function textValue(value: unknown): string {
  if (typeof value === "string") return value
  if (typeof value === "number") return String(value)
  return ""
}

export function VisibilityRuleForm({
  rule,
  earlier,
  onChange,
  idPrefix,
}: VisibilityRuleFormProps) {
  if (earlier.length === 0) {
    return (
      <div>
        <Label>{VISIBILITY_TITLE}</Label>
        <p className="text-[12.5px] text-muted-foreground">{VISIBILITY_NOTHING_EARLIER}</p>
      </div>
    )
  }

  const target = earlier.find((candidate) => candidate.key === rule?.item_key) ?? null
  const offered = target === null ? [] : conditionsFor(target)
  const condition = rule !== null && target !== null ? conditionOf(rule, offered) : null

  function pickTarget(key: string) {
    if (key === NO_TARGET) {
      onChange(null)
      return
    }
    // A question and no condition yet. Publishing would refuse it, which is
    // the right moment: mid-edit is not the moment to refuse anything.
    onChange({ item_key: key, op: "answered" })
  }

  function pickCondition(id: string) {
    if (rule === null || target === null) return
    const chosen = offered.find((candidate) => candidate.id === id)
    if (chosen === undefined) return
    onChange({
      item_key: rule.item_key,
      op: chosen.op,
      ...(chosen.needs === "none" && chosen.value === undefined ? {} : { value: chosen.value }),
    })
  }

  function setValue(value: unknown) {
    if (rule === null) return
    onChange({ item_key: rule.item_key, op: rule.op, value })
  }

  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <Label>{VISIBILITY_TITLE}</Label>

      <div>
        <Label htmlFor={`${idPrefix}-visible-target`} className="text-[12.5px]">
          {VISIBILITY_QUESTION_LABEL}
        </Label>
        <Select value={rule?.item_key ?? NO_TARGET} onValueChange={pickTarget}>
          <SelectTrigger
            id={`${idPrefix}-visible-target`}
            aria-label={VISIBILITY_QUESTION_LABEL}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_TARGET}>{VISIBILITY_ALWAYS}</SelectItem>
            {earlier.map((candidate) => (
              <SelectItem key={candidate.key} value={candidate.key}>
                {headingOf(candidate)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {target !== null && (
        <div>
          <Label htmlFor={`${idPrefix}-visible-condition`} className="text-[12.5px]">
            {VISIBILITY_CONDITION_LABEL}
          </Label>
          <Select value={condition?.id ?? ""} onValueChange={pickCondition}>
            <SelectTrigger
              id={`${idPrefix}-visible-condition`}
              aria-label={VISIBILITY_CONDITION_LABEL}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {offered.map((candidate) => (
                <SelectItem key={candidate.id} value={candidate.id}>
                  {candidate.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {target !== null && condition?.needs === "option" && (
        <div>
          <Label htmlFor={`${idPrefix}-visible-option`} className="text-[12.5px]">
            {VISIBILITY_VALUE_LABEL}
          </Label>
          <Select value={textValue(rule?.value)} onValueChange={(value) => setValue(value)}>
            <SelectTrigger id={`${idPrefix}-visible-option`} aria-label={VISIBILITY_VALUE_LABEL}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {optionsOf(target.config).map((option) => (
                <SelectItem key={option.key} value={option.key}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {condition?.needs === "number" && (
        <div>
          <Label htmlFor={`${idPrefix}-visible-number`} className="text-[12.5px]">
            {VISIBILITY_VALUE_LABEL}
          </Label>
          <Input
            id={`${idPrefix}-visible-number`}
            type="number"
            value={textValue(rule?.value)}
            onChange={(e) => setValue(numberOrUndefined(e.target.value))}
          />
        </div>
      )}

      {condition?.needs === "date" && (
        <div>
          <Label htmlFor={`${idPrefix}-visible-date`} className="text-[12.5px]">
            {VISIBILITY_VALUE_LABEL}
          </Label>
          <Input
            id={`${idPrefix}-visible-date`}
            type="date"
            value={textValue(rule?.value)}
            onChange={(e) => setValue(e.target.value || undefined)}
          />
        </div>
      )}

      {condition?.needs === "item" && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor={`${idPrefix}-visible-item`} className="text-[12.5px]">
              {VISIBILITY_ITEM_NUMBER_LABEL}
            </Label>
            <Input
              id={`${idPrefix}-visible-item`}
              type="number"
              value={textValue(asRecord(rule?.value).item)}
              onChange={(e) =>
                setValue({
                  ...asRecord(rule?.value),
                  item: numberOrUndefined(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-visible-item-score`} className="text-[12.5px]">
              {VISIBILITY_VALUE_LABEL}
            </Label>
            <Input
              id={`${idPrefix}-visible-item-score`}
              type="number"
              value={textValue(asRecord(rule?.value).value)}
              onChange={(e) =>
                setValue({
                  ...asRecord(rule?.value),
                  value: numberOrUndefined(e.target.value),
                })
              }
            />
          </div>
        </div>
      )}
    </div>
  )
}
