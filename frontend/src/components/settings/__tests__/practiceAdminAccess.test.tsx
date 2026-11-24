// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * usePracticeAdminAccess tests: who may change a practice-wide setting, and
 * which line the card shows, across loading, answered and failed /me/status.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook } from "@testing-library/react"

import { usePracticeAdminAccess } from "../practiceAdminAccess"

let statusResult: { data?: { is_practice_admin: boolean }; isError: boolean }

vi.mock("../useSettingsPreferences", () => ({
  useSettingsUserStatus: () => statusResult,
}))

describe("usePracticeAdminAccess", () => {
  beforeEach(() => {
    statusResult = { data: undefined, isError: false }
  })

  it("lets an admin edit with no line", () => {
    statusResult = { data: { is_practice_admin: true }, isError: false }
    expect(renderHook(() => usePracticeAdminAccess()).result.current).toEqual({
      canEdit: true,
      note: null,
    })
  })

  it("is admin-only for a clinician who is not an admin", () => {
    statusResult = { data: { is_practice_admin: false }, isError: false }
    expect(renderHook(() => usePracticeAdminAccess()).result.current).toEqual({
      canEdit: false,
      note: "admin-only",
    })
  })

  it("shows no line while the status loads", () => {
    expect(renderHook(() => usePracticeAdminAccess()).result.current).toEqual({
      canEdit: false,
      note: null,
    })
  })

  it("says it could not check, not that they lack permission, when the status fails", () => {
    statusResult = { data: undefined, isError: true }
    expect(renderHook(() => usePracticeAdminAccess()).result.current).toEqual({
      canEdit: false,
      note: "status-unavailable",
    })
  })

  it("keeps the answer it had when a refetch fails", () => {
    statusResult = { data: { is_practice_admin: true }, isError: true }
    expect(renderHook(() => usePracticeAdminAccess()).result.current).toEqual({
      canEdit: true,
      note: null,
    })
  })
})
