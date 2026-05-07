// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"
import { daysUntil, formatDueLabel, urgencyFor } from "../urgency"

const TEMPLATE: ComplianceTemplate = {
  item_type: "license",
  label: "Professional license",
  description: "",
  cadence_days: null,
  reminder_windows: [90, 60, 30, 0],
  multi_instance: false,
  min_edition: "core",
  sort_order: 10,
}

const NPI_TEMPLATE: ComplianceTemplate = {
  ...TEMPLATE,
  item_type: "npi",
  reminder_windows: [],
}

function makeItem(overrides: Partial<ComplianceItem> = {}): ComplianceItem {
  return {
    id: "item-1",
    item_type: "license",
    label: "Professional license",
    due_date: null,
    notes: null,
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

describe("urgency", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T12:00:00Z"))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe("daysUntil", () => {
    it("returns null when date is null", () => {
      expect(daysUntil(null)).toBeNull()
    })

    it("returns positive count for future dates", () => {
      expect(daysUntil("2026-05-17")).toBe(10)
    })

    it("returns negative count for past dates", () => {
      expect(daysUntil("2026-04-30")).toBe(-7)
    })
  })

  describe("urgencyFor", () => {
    it("flags overdue when due_date is in the past", () => {
      const item = makeItem({ due_date: "2026-04-01" })
      expect(urgencyFor(item, TEMPLATE)).toBe("overdue")
    })

    it("flags due-soon within the widest reminder window", () => {
      const item = makeItem({ due_date: "2026-07-01" }) // ~55 days out
      expect(urgencyFor(item, TEMPLATE)).toBe("due-soon")
    })

    it("flags upcoming when beyond the widest window", () => {
      const item = makeItem({ due_date: "2026-12-01" }) // far future
      expect(urgencyFor(item, TEMPLATE)).toBe("upcoming")
    })

    it("returns informational when template has no reminder windows", () => {
      const item = makeItem({ item_type: "npi", due_date: "2026-12-01" })
      expect(urgencyFor(item, NPI_TEMPLATE)).toBe("informational")
    })

    it("returns informational when due_date is null", () => {
      expect(urgencyFor(makeItem(), TEMPLATE)).toBe("informational")
    })
  })

  describe("formatDueLabel", () => {
    it("describes overdue dates", () => {
      expect(formatDueLabel(-3)).toBe("3 days overdue")
    })

    it("describes today and tomorrow distinctly", () => {
      expect(formatDueLabel(0)).toBe("due today")
      expect(formatDueLabel(1)).toBe("due tomorrow")
    })

    it("describes future dates in days", () => {
      expect(formatDueLabel(14)).toBe("due in 14 days")
    })

    it("returns empty string for null input", () => {
      expect(formatDueLabel(null)).toBe("")
    })
  })
})
