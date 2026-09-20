// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which questions on a form are shown, given the answers so far.
 *
 * The port of `backend/app/intake/visibility.py`, rule for rule. Both are
 * run against one table of cases — `__tests__/fixtures/intake_visibility_cases.json`,
 * written from the backend's copy by `scripts/sync_intake_visibility_fixtures.py`
 * — because two implementations of one rule drift, and two pinned to the
 * same cases cannot drift quietly.
 *
 * **The browser's answer is for display only.** Completion is the server's,
 * always — a question somebody was never shown and a question they skipped
 * look identical from here, and only the server knows which is which. So
 * the walk uses this to decide what to put on the screen and never to
 * decide whether the form can be handed in. The same goes for what happens
 * to an answer to a question that has since been hidden: the server
 * decides, when the form is submitted.
 *
 * The rules themselves, and why they are shaped this way, are documented on
 * the Python side. Four are worth repeating because they are what a reader
 * of this file would otherwise have to infer: evaluation is one forward
 * pass, a hidden question's answer is not offered to anything after it, an
 * unanswered trigger hides whatever operator is asking, and a measure
 * settles nothing until every one of its items is answered.
 */

/** Compares against the referenced item's own answer. */
export type AnswerOp = "eq" | "neq" | "in" | "gte" | "lte" | "answered"

/** Compares against a scored measure rather than an answer. */
export type InstrumentOp = "score_gte" | "score_lte" | "item_gte"

/**
 * Show the item carrying this rule only when the condition holds.
 *
 * Mirrors `VisibleWhen` in `backend/app/intake/rules.py`. `value` is absent
 * for `answered` and carried by every other operator; what a valid value
 * looks like depends on the item the rule points at, which is settled when
 * the version is published rather than here.
 */
export interface VisibleWhen {
  item_key: string
  op: AnswerOp | InstrumentOp
  value?: unknown
}

/**
 * One question, flattened to what deciding visibility needs.
 *
 * `instrumentItems` is how many items the measure this question asks has,
 * and is null for every question that is not one. It is passed in rather
 * than looked up because this module knows nothing about instruments; the
 * portal reads it off the form the server sent.
 */
export interface VisibilityItem {
  key: string
  rule: VisibleWhen | null
  instrumentItems?: number | null
}

/**
 * Decides which questions are shown, given each one's rule and every answer
 * given so far, keyed by item key. Pure: same inputs, same answer, no clock
 * and no network.
 */
export type VisibilityMap = (
  items: VisibilityItem[],
  answers: Record<string, unknown>,
) => Record<string, boolean>

/**
 * The fields a stored answer puts its comparable value in, in the order
 * they are looked for. A question answered with several choices carries
 * `keys` instead and is handled on its own.
 */
const ANSWER_FIELDS = ["key", "yes", "value", "text"] as const

const ORDER_OPS = new Set(["gte", "lte"])
const SCORE_OPS = new Set(["score_gte", "score_lte"])
const INSTRUMENT_OPS = new Set(["score_gte", "score_lte", "item_gte"])

/** Every key in `items` is answered, so a caller never reads a gap. */
export const evaluate: VisibilityMap = (items, answers) => {
  const shown: Record<string, boolean> = {}
  // Only the answers to questions this patient is actually shown.
  const live: Record<string, unknown> = {}
  const sizes = new Map(items.map((item) => [item.key, item.instrumentItems ?? null]))

  for (const item of items) {
    const visible = isVisible(item.rule, shown, live, sizes)
    shown[item.key] = visible
    if (visible && item.key in answers) live[item.key] = answers[item.key]
  }
  return shown
}

function isVisible(
  rule: VisibleWhen | null,
  shown: Record<string, boolean>,
  live: Record<string, unknown>,
  sizes: Map<string, number | null>,
): boolean {
  if (rule === null) return true
  // A rule naming nothing earlier on the form cannot be published, so this
  // is a version that drifted. Hiding is the safer reading: the practice
  // said "ask this only when", and a condition nobody can evaluate has not
  // been met.
  if (shown[rule.item_key] !== true) return false
  return conditionHolds(rule, live[rule.item_key], sizes.get(rule.item_key) ?? null)
}

function conditionHolds(
  rule: VisibleWhen,
  answer: unknown,
  instrumentItems: number | null,
): boolean {
  if (!isMapping(answer) || Object.keys(answer).length === 0) return false
  if (rule.op === "answered") return true
  if (INSTRUMENT_OPS.has(rule.op)) return measureHolds(rule, answer, instrumentItems)
  return answerHolds(rule, answer)
}

// ---------------------------------------------------------------------------
// Comparing against the answer itself
// ---------------------------------------------------------------------------

function answerHolds(rule: VisibleWhen, answer: Record<string, unknown>): boolean {
  const chosen = answer.keys
  if (Array.isArray(chosen)) {
    return pickedHolds(rule, chosen.filter((key): key is string => typeof key === "string"))
  }

  const field = ANSWER_FIELDS.find((candidate) => candidate in answer)
  if (field === undefined) return false
  const given = answer[field]

  if (ORDER_OPS.has(rule.op)) return orderedHolds(rule.op, given, rule.value)
  return matches(rule.op, given, rule.value)
}

/**
 * A question that takes several answers.
 *
 * "Show this when they said alcohol" is what a practice means by a rule on
 * a tick-list, so `eq` asks whether that answer is among the ones picked
 * rather than whether it is the only one. `in` is the same question asked
 * of several answers at once, and `neq` is `eq` negated.
 */
function pickedHolds(rule: VisibleWhen, chosen: string[]): boolean {
  const want = rule.value
  if (rule.op === "eq") return chosen.includes(want as string)
  if (rule.op === "neq") return !chosen.includes(want as string)
  if (rule.op === "in") {
    return Array.isArray(want) && chosen.some((key) => want.includes(key))
  }
  return false
}

/** `eq`, `neq` and `in` — the operators that ask about equality. */
function matches(op: string, given: unknown, want: unknown): boolean {
  if (op === "in") {
    return Array.isArray(want) && want.some((candidate) => same(given, candidate))
  }
  if (op === "eq") return same(given, want)
  return !same(given, want)
}

/** `gte` and `lte` — the operators that ask which came first. */
function orderedHolds(op: string, given: unknown, want: unknown): boolean {
  const order = compare(given, want)
  if (order === null) return false
  return op === "gte" ? order >= 0 : order <= 0
}

// ---------------------------------------------------------------------------
// Comparing against a scored measure
// ---------------------------------------------------------------------------

function measureHolds(
  rule: VisibleWhen,
  answer: Record<string, unknown>,
  size: number | null,
): boolean {
  const answered = measureScores(answer, size)
  if (answered === null) return false
  if (!SCORE_OPS.has(rule.op)) return oneItemHolds(rule.value, answered)
  const threshold = whole(rule.value)
  if (threshold === null) return false
  const total = answered.reduce((sum, score) => sum + score, 0)
  return rule.op === "score_gte" ? total >= threshold : total <= threshold
}

/**
 * Every item of the measure in order, or null until it is finished.
 *
 * Null covers all three ways there is no score to compare against: nothing
 * saved, an item still unanswered, and a caller that could not say how many
 * items the measure has.
 */
function measureScores(answer: Record<string, unknown>, size: number | null): number[] | null {
  const scores = answer.item_scores
  if (!isMapping(scores) || size === null) return null
  const answered: number[] = []
  for (let position = 1; position <= size; position += 1) {
    const score = whole(scores[String(position)])
    if (score === null) return null
    answered.push(score)
  }
  return answered
}

/** `item_gte`, whose value is `{item: <1-based>, value: <score>}`. */
function oneItemHolds(value: unknown, answered: number[]): boolean {
  if (!isMapping(value)) return false
  const index = whole(value.item)
  const threshold = whole(value.value)
  if (index === null || threshold === null) return false
  if (index < 1 || index > answered.length) return false
  return answered[index - 1] >= threshold
}

// ---------------------------------------------------------------------------
// Equality and ordering that do not lie about types
// ---------------------------------------------------------------------------

/** Equality that will not read `true` as `1` or a date as a string. */
function same(given: unknown, want: unknown): boolean {
  if (typeof given === "boolean" || typeof want === "boolean") return given === want
  const order = compare(given, want)
  if (order !== null) return order === 0
  return given === want
}

/** -1/0/1, or null when the two cannot be ordered. */
function compare(given: unknown, want: unknown): number | null {
  const leftNumber = asNumber(given)
  const rightNumber = asNumber(want)
  if (leftNumber !== null && rightNumber !== null) {
    return Math.sign(leftNumber - rightNumber)
  }
  const leftDate = asDate(given)
  const rightDate = asDate(want)
  if (leftDate !== null && rightDate !== null) return Math.sign(leftDate - rightDate)
  return null
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function whole(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

/** A day written down as `2026-03-14`, as a stamp that can be ordered. */
function asDate(value: unknown): number | null {
  if (typeof value !== "string") return null
  const parts = ISO_DATE.exec(value)
  if (parts === null) return null
  const [year, month, day] = [Number(parts[1]), Number(parts[2]), Number(parts[3])]
  const stamp = Date.UTC(year, month - 1, day)
  const read = new Date(stamp)
  const real =
    read.getUTCFullYear() === year && read.getUTCMonth() + 1 === month && read.getUTCDate() === day
  return real ? stamp : null
}

function isMapping(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** The rule an item carries, or null when it carries none. */
export function ruleOf(config: Record<string, unknown>): VisibleWhen | null {
  const rule = config.visible_when
  if (typeof rule !== "object" || rule === null) return null
  const candidate = rule as Record<string, unknown>
  if (typeof candidate.item_key !== "string" || typeof candidate.op !== "string") return null
  return candidate as unknown as VisibleWhen
}
