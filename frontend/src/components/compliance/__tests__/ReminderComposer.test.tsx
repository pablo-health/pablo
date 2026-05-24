// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReminderComposer } from "../ReminderComposer"
import type {
  ComplianceItem,
  ComplianceTemplate,
} from "@/types/compliance"

const createMutate = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const updateMutate = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))

vi.mock("@/hooks/useCompliance", () => ({
  useCreateComplianceItem: () => ({
    mutateAsync: createMutate,
    isPending: false,
  }),
  useUpdateComplianceItem: () => ({
    mutateAsync: updateMutate,
    isPending: false,
  }),
}))

const LICENSE: ComplianceTemplate = {
  item_type: "license",
  label: "Professional license",
  description: "State board license to practice.",
  cadence_days: null,
  reminder_windows: [90, 60, 30, 0],
  multi_instance: false,
  min_edition: "core",
  sort_order: 10,
}

const BAA: ComplianceTemplate = {
  item_type: "baa",
  label: "Business Associate Agreement",
  description: "BAA with a vendor that handles PHI. Add one per vendor.",
  cadence_days: null,
  reminder_windows: [60, 30, 0],
  multi_instance: true,
  min_edition: "solo",
  sort_order: 70,
}

const TEMPLATES = [LICENSE, BAA]

function makeItem(overrides: Partial<ComplianceItem> = {}): ComplianceItem {
  return {
    id: "item-1",
    item_type: "license",
    label: "Professional license",
    due_date: "2027-01-01",
    notes: null,
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

function renderComposer(props: {
  items?: ComplianceItem[]
  initialItem?: ComplianceItem | null
}) {
  const onOpenChange = vi.fn()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const utils = render(
    <QueryClientProvider client={client}>
      <ReminderComposer
        open
        onOpenChange={onOpenChange}
        templates={TEMPLATES}
        items={props.items ?? []}
        initialItem={props.initialItem ?? null}
      />
    </QueryClientProvider>,
  )
  return { onOpenChange, ...utils }
}

describe("ReminderComposer", () => {
  beforeEach(() => {
    createMutate.mockClear()
    updateMutate.mockClear()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe("browse mode", () => {
    it("groups templates by category and shows them all", () => {
      renderComposer({})

      // Both templates from different categories are visible from the start —
      // the whole point of the redesign is no forced linear march.
      expect(screen.getByText("Professional license")).toBeInTheDocument()
      expect(
        screen.getByText("Business Associate Agreement"),
      ).toBeInTheDocument()
      expect(screen.getByText(/credentials & licensure/i)).toBeInTheDocument()
      expect(screen.getByText(/vendors & baas/i)).toBeInTheDocument()
    })

    it("marks tracked single-instance templates with a Tracked badge", () => {
      renderComposer({ items: [makeItem()] })
      expect(screen.getByText(/^tracked$/i)).toBeInTheDocument()
    })

    it("shows a per-instance edit list under tracked multi-instance templates", () => {
      const items = [
        makeItem({
          id: "baa-1",
          item_type: "baa",
          label: "BAA — Spruce Health",
        }),
        makeItem({
          id: "baa-2",
          item_type: "baa",
          label: "BAA — SimplePractice",
        }),
      ]
      renderComposer({ items })
      expect(screen.getByText("2 tracked")).toBeInTheDocument()
      expect(screen.getByText("BAA — Spruce Health")).toBeInTheDocument()
      expect(screen.getByText("BAA — SimplePractice")).toBeInTheDocument()
    })

    it("opens edit mode pre-filled when an existing tracked single-instance card is clicked", () => {
      const existing = makeItem({ due_date: "2027-06-30" })
      renderComposer({ items: [existing] })

      // Click the card body (the title button).
      fireEvent.click(screen.getByText("Professional license"))

      // Edit mode shows the back link and the existing date.
      expect(
        screen.getByRole("button", { name: /all reminders/i }),
      ).toBeInTheDocument()
      const dueInput = screen.getByLabelText(
        /expiration date/i,
      ) as HTMLInputElement
      expect(dueInput.value).toBe("2027-06-30")
    })
  })

  describe("create flow", () => {
    it("creates a new single-instance item from a fresh template card", async () => {
      renderComposer({})

      fireEvent.click(screen.getByText("Professional license"))

      const dueInput = screen.getByLabelText(/expiration date/i)
      fireEvent.change(dueInput, { target: { value: "2027-12-01" } })

      fireEvent.click(screen.getByRole("button", { name: /save reminder/i }))

      // Mutation called with the template's canonical label and the chosen date.
      expect(createMutate).toHaveBeenCalledTimes(1)
      expect(createMutate).toHaveBeenCalledWith({
        item_type: "license",
        label: "Professional license",
        due_date: "2027-12-01",
        notes: null,
      })
    })

    it("multi-instance: 'Save & add another' resets to a fresh form on the same template", async () => {
      renderComposer({})

      // Click the BAA card body to land in its edit view.
      fireEvent.click(screen.getByText("Business Associate Agreement"))

      // Label is required for multi-instance.
      const labelInput = screen.getByLabelText(/^label$/i) as HTMLInputElement
      fireEvent.change(labelInput, { target: { value: "BAA — Spruce" } })

      const dueInput = screen.getByLabelText(/expiration date/i)
      fireEvent.change(dueInput, { target: { value: "2027-09-01" } })

      const addAnother = screen.getByRole("button", {
        name: /save & add another/i,
      })
      fireEvent.click(addAnother)

      await waitFor(() =>
        expect(createMutate).toHaveBeenCalledWith({
          item_type: "baa",
          label: "BAA — Spruce",
          due_date: "2027-09-01",
          notes: null,
        }),
      )

      // Still on the BAA edit form, with cleared label — ready for the next one.
      // The EditView is keyed on a per-mount epoch counter so the
      // re-entry re-initializes form state from props.
      await waitFor(() => {
        const labelAfter = screen.getByLabelText(/^label$/i) as HTMLInputElement
        expect(labelAfter.value).toBe("")
      })
      expect(
        screen.getByText("Business Associate Agreement"),
      ).toBeInTheDocument()
    })
  })

  describe("edit flow", () => {
    it("opens directly into edit mode when initialItem is supplied", () => {
      renderComposer({
        items: [makeItem({ due_date: "2027-03-15" })],
        initialItem: makeItem({ due_date: "2027-03-15" }),
      })
      // Skip the browse view entirely — already on the form.
      expect(screen.getByText(/edit · professional license/i)).toBeInTheDocument()
      const due = screen.getByLabelText(/expiration date/i) as HTMLInputElement
      expect(due.value).toBe("2027-03-15")
    })

    it("calls update (not create) on save when editing an existing item", async () => {
      const existing = makeItem({ due_date: "2027-03-15" })
      renderComposer({ items: [existing], initialItem: existing })

      fireEvent.change(screen.getByLabelText(/expiration date/i), {
        target: { value: "2027-04-01" },
      })
      fireEvent.click(screen.getByRole("button", { name: /save changes/i }))

      expect(updateMutate).toHaveBeenCalledTimes(1)
      expect(updateMutate).toHaveBeenCalledWith({
        id: "item-1",
        payload: {
          item_type: "license",
          label: "Professional license",
          due_date: "2027-04-01",
          notes: null,
        },
      })
      expect(createMutate).not.toHaveBeenCalled()
    })

    it("Back returns to the browse view without saving", () => {
      renderComposer({})
      fireEvent.click(screen.getByText("Professional license"))
      expect(
        screen.getByRole("button", { name: /all reminders/i }),
      ).toBeInTheDocument()

      fireEvent.click(screen.getByRole("button", { name: /all reminders/i }))

      // Back in browse: header copy + the BAA card are visible again.
      expect(screen.getByText(/pick what to track/i)).toBeInTheDocument()
      const browse = screen.getByTestId("composer-browse")
      expect(
        within(browse).getByText("Business Associate Agreement"),
      ).toBeInTheDocument()
      expect(createMutate).not.toHaveBeenCalled()
      expect(updateMutate).not.toHaveBeenCalled()
    })
  })

  it("Cancel closes the dialog", () => {
    const { onOpenChange } = renderComposer({})
    fireEvent.click(screen.getByText("Professional license"))
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
