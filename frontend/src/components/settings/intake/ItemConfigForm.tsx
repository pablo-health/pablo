// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import type { Instrument } from "@/types/instruments"
import type { ChoiceOption, ItemConfig, ItemType } from "@/types/intakePackets"
import {
  BLANK_FORM_PICKER_LABEL,
  CARD_COLLECT_FIELDS_HELP,
  CARD_COLLECT_FIELDS_LABEL,
  CARD_SIDES_BOTH,
  CARD_SIDES_FRONT,
  CARD_SIDES_LABEL,
  DOCUMENT_PICKER_LABEL,
  MEASURE_NEEDS_PERMISSION,
  NO_BLANK_FORM_CHOICE,
  NO_BLANK_FORMS,
  NO_PUBLISHED_DOCUMENTS,
} from "./intakeCopy"

interface ItemConfigFormProps {
  itemType: ItemType
  config: ItemConfig
  onChange: (next: ItemConfig) => void
  /** Prefix for the generated field ids, so two open items do not collide. */
  idPrefix: string
  /**
   * The documents this form may ask somebody to sign — the practice's
   * published ones. Passed in rather than fetched here so this stays a
   * component that renders what it is given; the card that owns the form
   * already holds the list.
   */
  documents?: PublishedDocument[]
  /**
   * The instruments the engine knows, with this practice's permissions on
   * them. Passed in for the same reason as the documents: the card that owns
   * the form already holds the list, and the server is what decides which
   * measures are askable and which are waiting on a permission.
   */
  instruments?: Instrument[]
  /**
   * The practice's own blank forms, for a document question to offer. Same
   * arrangement as `documents` above: the card that owns the form holds the
   * list, and this component renders what it is given.
   */
  blankForms?: OfferableBlankForm[]
}

/** One document a consent item can point at. */
export interface PublishedDocument {
  document_key: string
  title: string
}

/** One blank form a document question can offer for download. */
export interface OfferableBlankForm {
  id: string
  title: string
}

/** The value a "no blank form" choice stores, since a Select needs one. */
const NO_BLANK_FORM = "none"

function text(config: ItemConfig, key: string): string {
  const value = config[key]
  return typeof value === "string" ? value : ""
}

function num(config: ItemConfig, key: string): string {
  const value = config[key]
  return typeof value === "number" ? String(value) : ""
}

function options(config: ItemConfig): ChoiceOption[] {
  const value = config.options
  return Array.isArray(value) ? (value as ChoiceOption[]) : []
}

/**
 * Turn a label into a stable answer key.
 *
 * The key is what gets stored and what a rule compares against, so it is
 * derived once when the answer is added and never re-derived on relabel — a
 * therapist fixing a typo must not silently repoint anything.
 */
function keyFor(label: string, taken: string[]): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40)
  const stem = base || "answer"
  if (!taken.includes(stem)) return stem
  let n = 2
  while (taken.includes(`${stem}_${n}`)) n += 1
  return `${stem}_${n}`
}

function ChoiceOptions({ config, onChange, idPrefix }: Omit<ItemConfigFormProps, "itemType">) {
  const current = options(config)

  function update(index: number, label: string) {
    const next = current.map((o, i) => (i === index ? { ...o, label } : o))
    onChange({ ...config, options: next })
  }

  function add() {
    const label = `Answer ${current.length + 1}`
    onChange({
      ...config,
      options: [...current, { key: keyFor(label, current.map((o) => o.key)), label }],
    })
  }

  function remove(index: number) {
    onChange({ ...config, options: current.filter((_, i) => i !== index) })
  }

  return (
    <div className="space-y-2">
      <Label>Answers</Label>
      {current.map((option, index) => (
        <div key={option.key} className="flex items-center gap-2">
          <Input
            aria-label={`Answer ${index + 1}`}
            value={option.label}
            onChange={(e) => update(index, e.target.value)}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove answer ${index + 1}`}
            onClick={() => remove(index)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={add} id={`${idPrefix}-add-option`}>
        <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
        Add an answer
      </Button>
    </div>
  )
}

/**
 * The settings one question needs, chosen by its type.
 *
 * Every branch writes into the same open `config` record, which is what the
 * API stores and the server validates at publish. Nothing here decides whether
 * a configuration is valid — a half-filled draft is a normal thing to save.
 */
export function ItemConfigForm({
  itemType,
  config,
  onChange,
  idPrefix,
  documents = [],
  instruments = [],
  blankForms = [],
}: ItemConfigFormProps) {
  const set = (key: string, value: unknown) => onChange({ ...config, [key]: value })
  const setNumber = (key: string, raw: string) =>
    set(key, raw === "" ? undefined : Number(raw))

  switch (itemType) {
    case "section":
      return (
        <div>
          <Label htmlFor={`${idPrefix}-title`}>Heading</Label>
          <Input
            id={`${idPrefix}-title`}
            value={text(config, "title")}
            onChange={(e) => set("title", e.target.value)}
          />
        </div>
      )

    case "instructions":
      return (
        <div>
          <Label htmlFor={`${idPrefix}-body`}>What they read</Label>
          <Textarea
            id={`${idPrefix}-body`}
            rows={4}
            value={text(config, "body_markdown")}
            onChange={(e) => set("body_markdown", e.target.value)}
          />
        </div>
      )

    case "free_text":
      return (
        <div>
          <Label htmlFor={`${idPrefix}-max-len`}>Longest answer, in characters</Label>
          <Input
            id={`${idPrefix}-max-len`}
            type="number"
            value={num(config, "max_len")}
            onChange={(e) => setNumber("max_len", e.target.value)}
          />
        </div>
      )

    case "single_choice":
      return <ChoiceOptions config={config} onChange={onChange} idPrefix={idPrefix} />

    case "multi_choice":
      return (
        <div className="space-y-3">
          <ChoiceOptions config={config} onChange={onChange} idPrefix={idPrefix} />
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor={`${idPrefix}-min`}>Fewest they may pick</Label>
              <Input
                id={`${idPrefix}-min`}
                type="number"
                value={num(config, "min")}
                onChange={(e) => setNumber("min", e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor={`${idPrefix}-max`}>Most they may pick</Label>
              <Input
                id={`${idPrefix}-max`}
                type="number"
                value={num(config, "max")}
                onChange={(e) => setNumber("max", e.target.value)}
              />
            </div>
          </div>
        </div>
      )

    case "yes_no":
      return (
        <div>
          <Label htmlFor={`${idPrefix}-follow-up`}>If yes, also ask</Label>
          <Input
            id={`${idPrefix}-follow-up`}
            value={text(config, "follow_up_label")}
            placeholder="Leave empty to ask nothing more"
            onChange={(e) => set("follow_up_label", e.target.value || undefined)}
          />
        </div>
      )

    case "scale":
      return (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor={`${idPrefix}-min`}>Bottom of the scale</Label>
            <Input
              id={`${idPrefix}-min`}
              type="number"
              value={num(config, "min")}
              onChange={(e) => setNumber("min", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-max`}>Top of the scale</Label>
            <Input
              id={`${idPrefix}-max`}
              type="number"
              value={num(config, "max")}
              onChange={(e) => setNumber("max", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-min-label`}>Word at the bottom</Label>
            <Input
              id={`${idPrefix}-min-label`}
              value={text(config, "min_label")}
              onChange={(e) => set("min_label", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-max-label`}>Word at the top</Label>
            <Input
              id={`${idPrefix}-max-label`}
              value={text(config, "max_label")}
              onChange={(e) => set("max_label", e.target.value)}
            />
          </div>
        </div>
      )

    case "number":
      return (
        <div className="grid grid-cols-3 gap-3">
          <div>
            <Label htmlFor={`${idPrefix}-min`}>Smallest</Label>
            <Input
              id={`${idPrefix}-min`}
              type="number"
              value={num(config, "min")}
              onChange={(e) => setNumber("min", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-max`}>Largest</Label>
            <Input
              id={`${idPrefix}-max`}
              type="number"
              value={num(config, "max")}
              onChange={(e) => setNumber("max", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-unit`}>Unit</Label>
            <Input
              id={`${idPrefix}-unit`}
              value={text(config, "unit")}
              onChange={(e) => set("unit", e.target.value || undefined)}
            />
          </div>
        </div>
      )

    case "date":
      return (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor={`${idPrefix}-min`}>Earliest</Label>
            <Input
              id={`${idPrefix}-min`}
              type="date"
              value={text(config, "min")}
              onChange={(e) => set("min", e.target.value || undefined)}
            />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-max`}>Latest</Label>
            <Input
              id={`${idPrefix}-max`}
              type="date"
              value={text(config, "max")}
              onChange={(e) => set("max", e.target.value || undefined)}
            />
          </div>
        </div>
      )

    case "instrument": {
      // Three states, and only two of them are the practice's to change. A
      // measure the engine has the questions for is offered; one whose use is
      // restricted is offered greyed until the practice records its
      // permission; one the engine will never carry the questions for is not
      // on the list at all, because there is nothing here that would make it
      // askable. The server answers all three — a second opinion computed
      // here would be free to drift from the one that refuses the publish.
      const askable = instruments.filter((instrument) => instrument.can_ask_on_a_form)
      const waiting = askable.some(
        (instrument) => instrument.rights === "attestation_required" && !instrument.attested
      )
      return (
        <div>
          <Label htmlFor={`${idPrefix}-code`}>Which measure</Label>
          <Select value={text(config, "code")} onValueChange={(value) => set("code", value)}>
            <SelectTrigger id={`${idPrefix}-code`} aria-label="Which measure">
              <SelectValue placeholder="Choose a measure" />
            </SelectTrigger>
            <SelectContent>
              {askable.map((instrument) => (
                <SelectItem
                  key={instrument.code}
                  value={instrument.code}
                  disabled={instrument.rights === "attestation_required" && !instrument.attested}
                >
                  {instrument.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {waiting && (
            <p className="mt-1 text-[12.5px] text-muted-foreground">
              {MEASURE_NEEDS_PERMISSION}
            </p>
          )}
        </div>
      )
    }

    case "consent_document":
      // A document, not a version of one. Which wording a patient actually
      // signs is decided when the form is published, so a practice that
      // revises a document afterwards does not have to touch the form.
      return (
        <div>
          <Label htmlFor={`${idPrefix}-document`}>{DOCUMENT_PICKER_LABEL}</Label>
          {documents.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">{NO_PUBLISHED_DOCUMENTS}</p>
          ) : (
            <Select
              value={text(config, "document_key")}
              onValueChange={(value) => set("document_key", value)}
            >
              <SelectTrigger id={`${idPrefix}-document`} aria-label={DOCUMENT_PICKER_LABEL}>
                <SelectValue placeholder="Choose a document" />
              </SelectTrigger>
              <SelectContent>
                {documents.map((document) => (
                  <SelectItem key={document.document_key} value={document.document_key}>
                    {document.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )

    case "insurance_card":
      // What to ask for is the item's own question. What is settable is how
      // many photographs to ask for, and whether to ask for the plan in
      // words as well as in a photograph.
      return (
        <div className="space-y-3">
          <div>
            <Label htmlFor={`${idPrefix}-sides`}>{CARD_SIDES_LABEL}</Label>
            <Select
              value={text(config, "sides") || "both"}
              onValueChange={(value) => set("sides", value)}
            >
              <SelectTrigger id={`${idPrefix}-sides`} aria-label={CARD_SIDES_LABEL}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="both">{CARD_SIDES_BOTH}</SelectItem>
                <SelectItem value="front">{CARD_SIDES_FRONT}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <label
            htmlFor={`${idPrefix}-collect-fields`}
            className="flex items-start gap-2 text-[13px] text-foreground"
          >
            <input
              type="checkbox"
              id={`${idPrefix}-collect-fields`}
              className="mt-0.5 h-4 w-4"
              checked={config.collect_fields === true}
              onChange={(e) => set("collect_fields", e.target.checked || undefined)}
            />
            <span>
              {CARD_COLLECT_FIELDS_LABEL}
              <span className="block text-[12.5px] text-muted-foreground">
                {CARD_COLLECT_FIELDS_HELP}
              </span>
            </span>
          </label>
        </div>
      )

    case "document_request":
      // What to ask for is the item's own question. What is settable is the
      // paper fallback: a practice that still works from paper can offer
      // its own form to download before asking for the filled-in copy back.
      return (
        <div>
          <Label htmlFor={`${idPrefix}-blank-form`}>{BLANK_FORM_PICKER_LABEL}</Label>
          {blankForms.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">{NO_BLANK_FORMS}</p>
          ) : (
            <Select
              value={text(config, "blank_form_id") || NO_BLANK_FORM}
              onValueChange={(value) =>
                set("blank_form_id", value === NO_BLANK_FORM ? undefined : value)
              }
            >
              <SelectTrigger id={`${idPrefix}-blank-form`} aria-label={BLANK_FORM_PICKER_LABEL}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_BLANK_FORM}>{NO_BLANK_FORM_CHOICE}</SelectItem>
                {blankForms.map((form) => (
                  <SelectItem key={form.id} value={form.id}>
                    {form.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      )

    default:
      // demographics, reason, emergency_contact and guardian have nothing
      // for a practice to set: the engine fixes their shape.
      return null
  }
}
