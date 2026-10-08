// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which part of a proposed chart text is new.
 *
 * A proposal keeps the chart's text and merges the change into it, so the
 * words it shares with the chart at the start and at the end are unchanged
 * and the run between them is the change. Words, not characters, so a
 * highlight never starts mid-word.
 */

export interface ChangeParts {
  before: string
  changed: string
  after: string
}

function words(text: string): string[] {
  return text.split(/(\s+)/).filter((part) => part !== "")
}

export function changeParts(current: string | null, proposed: string): ChangeParts {
  const old = words(current ?? "")
  const next = words(proposed)
  let start = 0
  while (start < old.length && start < next.length && old[start] === next[start]) start++
  let end = 0
  while (
    end < old.length - start &&
    end < next.length - start &&
    old[old.length - 1 - end] === next[next.length - 1 - end]
  )
    end++
  return {
    before: next.slice(0, start).join(""),
    changed: next.slice(start, next.length - end).join(""),
    after: next.slice(next.length - end).join(""),
  }
}
