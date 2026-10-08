// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChartHistoryTab Component Tests
 *
 * An empty field reads "Not recorded"; a recorded one shows where it came
 * from and the values it replaced; editing and removing send the field's
 * key; read-only hides every write.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ChartHistoryTab } from "../ChartHistoryTab"
import type { HistoryField, HistoryGroup } from "@/types/chartHistory"

let groups: HistoryGroup[] = []
const setField = vi.fn()
const removeField = vi.fn()
const showToast = vi.fn()

const mutation = (mutateAsync: typeof setField) => ({ mutateAsync, isPending: false })

vi.mock("@/hooks/useChartHistory", () => ({
  usePatientChartHistory: () => ({ data: { groups }, isLoading: false, error: null }),
  useSetHistoryField: () => mutation(setField),
  useRemoveHistoryField: () => mutation(removeField),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast }),
}))

function field(overrides: Partial<HistoryField>): HistoryField {
  return {
    key: "living_situation",
    label: "Living situation",
    text: null,
    updated_at: null,
    updated_by: null,
    source_note_id: null,
    source_note_date: null,
    earlier: [],
    ...overrides,
  }
}

function withFields(...fields: HistoryField[]) {
  groups = [{ key: "social_history", label: "Social history and supports", fields }]
}

describe("ChartHistoryTab", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setField.mockResolvedValue(undefined)
    removeField.mockResolvedValue(undefined)
    withFields(field({}))
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it("shows each group and reads an empty field as not recorded", () => {
    render(<ChartHistoryTab patientId="patient_1" />)

    expect(screen.getByRole("heading", { name: "Social history and supports" })).toBeInTheDocument()
    expect(screen.getByText("Not recorded")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add Living situation" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument()
  })

  it("shows a recorded value with when it was updated and the note it came from", () => {
    withFields(
      field({
        text: "Lives alone since the separation.",
        updated_at: "2026-09-02T15:00:00Z",
        source_note_id: "note_1",
        source_note_date: "2026-07-14T15:00:00Z",
      }),
    )
    render(<ChartHistoryTab patientId="patient_1" />)

    expect(screen.getByText("Lives alone since the separation.")).toBeInTheDocument()
    expect(screen.getByText(/^Last updated .*2026, from the note of .*2026$/)).toBeInTheDocument()
  })

  it("says only when it was updated when no note is behind it", () => {
    withFields(field({ text: "Lives alone.", updated_at: "2026-09-02T15:00:00Z" }))
    render(<ChartHistoryTab patientId="patient_1" />)

    expect(screen.getByText(/^Last updated .*2026$/)).toBeInTheDocument()
  })

  it("keeps earlier values behind an expander", () => {
    withFields(
      field({
        text: "Lives alone.",
        updated_at: "2026-09-02T15:00:00Z",
        earlier: [
          {
            text: "Lives with spouse.",
            written_at: "2026-07-14T15:00:00Z",
            written_by: "user_1",
            replaced_at: "2026-09-02T15:00:00Z",
            replaced_by: "user_1",
            source_note_id: null,
          },
        ],
      }),
    )
    render(<ChartHistoryTab patientId="patient_1" />)

    expect(screen.getByText("Earlier values (1)")).toBeInTheDocument()
    expect(screen.getByText("Lives with spouse.")).toBeInTheDocument()
  })

  it("saves an edit to the field's key", async () => {
    withFields(field({ text: "Lives with spouse.", updated_at: "2026-07-14T15:00:00Z" }))
    render(<ChartHistoryTab patientId="patient_1" />)

    fireEvent.click(screen.getByRole("button", { name: "Edit Living situation" }))
    fireEvent.change(screen.getByLabelText("Living situation"), {
      target: { value: "  Separated; lives alone.  " },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(setField).toHaveBeenCalledWith({
        patientId: "patient_1",
        key: "living_situation",
        data: { text: "Separated; lives alone." },
      }),
    )
  })

  it("does not save an empty value", () => {
    render(<ChartHistoryTab patientId="patient_1" />)

    fireEvent.click(screen.getByRole("button", { name: "Add Living situation" }))
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("removes a value after confirming", async () => {
    withFields(field({ text: "Lives alone.", updated_at: "2026-07-14T15:00:00Z" }))
    vi.stubGlobal("confirm", vi.fn(() => true))
    render(<ChartHistoryTab patientId="patient_1" />)

    fireEvent.click(screen.getByRole("button", { name: "Remove Living situation" }))

    await waitFor(() =>
      expect(removeField).toHaveBeenCalledWith({ patientId: "patient_1", key: "living_situation" }),
    )
  })

  it("hides every write when read-only", () => {
    withFields(field({ text: "Lives alone.", updated_at: "2026-07-14T15:00:00Z" }))
    vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
    render(<ChartHistoryTab patientId="patient_1" />)

    expect(screen.getByText("Lives alone.")).toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})
