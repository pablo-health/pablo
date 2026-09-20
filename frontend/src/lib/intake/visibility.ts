// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether one question on a form is shown, given the answers so far.
 *
 * The port of the server's seam (`backend/app/intake/completion.py`), and
 * today it is the same stub: rules have a stored shape and are checked when
 * a version is published, but nothing evaluates one yet, so every question
 * is shown to everybody. A form behaves exactly as one with no rules on it
 * does.
 *
 * **The browser's answer is for display only.** Completion is the server's,
 * always — a question somebody was never shown and a question they skipped
 * look identical from here, and only the server knows which is which. So
 * the walk uses this to decide what to put on the screen and never to
 * decide whether the form can be handed in.
 *
 * It exists now, ahead of anything that reads a rule, because of what
 * changes when evaluation lands: the renderer's walk takes it as a
 * parameter, so the rule engine arrives as one new implementation and one
 * set of fixtures shared with the Python side, with the walk untouched.
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
 * Decides whether one question is shown, given its rule and every answer
 * given so far, keyed by item key. Pure: same inputs, same answer, no
 * clock and no network.
 */
export type VisibilityRule = (
  rule: VisibleWhen | null,
  answers: Record<string, unknown>,
) => boolean

/** Show every question, whatever rule it carries. The v1 implementation. */
export const everyItemVisible: VisibilityRule = () => true

/** The rule an item carries, or null when it carries none. */
export function ruleOf(config: Record<string, unknown>): VisibleWhen | null {
  const rule = config.visible_when
  if (typeof rule !== "object" || rule === null) return null
  const candidate = rule as Record<string, unknown>
  if (typeof candidate.item_key !== "string" || typeof candidate.op !== "string") return null
  return candidate as unknown as VisibleWhen
}
