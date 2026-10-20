// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * `loaded` settles for every note, including one with no note type (its
 * definition query is never enabled, so it is "pending" for ever) and one
 * that states no diagnoses (the problem-list query is never enabled). The
 * sign dialog waits on `loaded`; a note it never settled for could never be
 * signed from the page, which three specs found the first time round.
 */

import { describe, expect, it, vi } from "vitest"
import { renderHook } from "@testing-library/react"
import { useChartUpdates } from "../UpdateChart"
import { createMockNote } from "@/test/factories"

const queries = {
  proposals: { data: { data: [], run: null }, isPending: false, isLoading: false },
  // The shape a query that is not enabled has: pending, not loading.
  noteType: { data: undefined as unknown, isPending: true, isLoading: false },
  problems: { data: undefined as unknown, isPending: true, isLoading: false },
}

vi.mock("@/hooks/useChartProposals", () => ({ useChartProposals: () => queries.proposals }))
vi.mock("@/hooks/useNoteTypes", () => ({ useNoteType: () => queries.noteType }))
vi.mock("@/hooks/useProblems", () => ({ usePatientProblems: () => queries.problems }))
vi.mock("@/lib/access/readOnlyMode", () => ({ useReadOnlyMode: () => ({ readOnly: false }) }))

describe("useChartUpdates", () => {
  it("is not loaded while the proposals are still being read", () => {
    queries.proposals = { data: { data: [], run: null }, isPending: true, isLoading: true }
    const { result } = renderHook(() => useChartUpdates(createMockNote()))
    expect(result.current.loaded).toBe(false)
    queries.proposals = { data: { data: [], run: null }, isPending: false, isLoading: false }
  })

  it("is loaded for a note whose type and problem list are never asked for", () => {
    const { result } = renderHook(() => useChartUpdates(createMockNote()))
    expect(result.current.loaded).toBe(true)
    expect(result.current.undecided).toBe(0)
  })

  it("is loaded with no note at all", () => {
    const { result } = renderHook(() => useChartUpdates(undefined))
    expect(result.current.loaded).toBe(true)
  })
})
