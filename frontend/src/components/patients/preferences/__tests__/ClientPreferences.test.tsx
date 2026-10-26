// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "How to work with <client>" on the chart: rows grouped by domain with where
 * they came from, the client's own name and pronouns shown as they are, add,
 * edit and remove, and the line that keeps clinical content off it.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { ClientPreferences } from "../ClientPreferences"
import type { Preference } from "@/types/patientPreferences"

const usePatientPreferences = vi.hoisted(() => vi.fn())
const addPreference = vi.hoisted(() => vi.fn())
const updatePreference = vi.hoisted(() => vi.fn())
const retirePreference = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/usePatientPreferences", () => ({
  usePatientPreferences: (...args: unknown[]) => usePatientPreferences(...args),
  useAddPreference: () => ({ mutateAsync: addPreference, isPending: false }),
  useUpdatePreference: () => ({ mutateAsync: updatePreference, isPending: false }),
  useRetirePreference: () => ({ mutateAsync: retirePreference, isPending: false }),
}))

function row(overrides: Partial<Preference>): Preference {
  return {
    id: "pref-1",
    patient_id: "p-maya",
    domain: "scheduling",
    kind: "scheduling.availability",
    kind_label: "Times that suit",
    value: { days: ["mon", "thu"], times: ["afternoon"], modality: "in_person" },
    text: null,
    strength: "soft",
    summary: null,
    source: "clinician",
    stated_at: "2026-10-12T15:00:00Z",
    confirmed_at: null,
    retired_at: null,
    created_at: "2026-10-12T15:00:00Z",
    updated_at: "2026-10-12T15:00:00Z",
    ...overrides,
  }
}

const AFTERNOONS = row({ text: "School pickup in the mornings" })
const PARKING = row({
  id: "pref-2",
  domain: "practical",
  kind: "practical.note",
  kind_label: "Practical",
  value: null,
  strength: null,
  text: "Parking is at the back",
  source: "assistant",
  stated_at: "2026-03-02T15:00:00Z",
})
const INTERPRETER = row({
  id: "pref-3",
  domain: "communication",
  kind: "communication.interpreter",
  kind_label: "Interpreter",
  value: { language: "Spanish" },
  strength: null,
  summary: "Needs a Spanish interpreter",
  source: "intake",
})

function renderCard(rows: Preference[] = [AFTERNOONS, PARKING]) {
  usePatientPreferences.mockReturnValue({ data: { data: rows, total: rows.length } })
  return render(
    <ClientPreferences patientId="p-maya" name="Mia" preferredName="Mia" pronouns="they/them" />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  addPreference.mockResolvedValue(row({}))
  updatePreference.mockResolvedValue(row({}))
  retirePreference.mockResolvedValue(row({}))
})

describe("ClientPreferences", () => {
  it("groups rows by domain and says where each came from", () => {
    renderCard([AFTERNOONS, PARKING, INTERPRETER])

    expect(screen.getByRole("heading", { name: "How to work with Mia" })).toBeInTheDocument()
    expect(screen.getByText("Goes by Mia · they/them")).toBeInTheDocument()
    expect(screen.getByText("Scheduling")).toBeInTheDocument()
    expect(screen.getByText("Practical")).toBeInTheDocument()

    const afternoons = screen.getByTestId("preference-pref-1")
    expect(afternoons).toHaveTextContent("Prefers: Mon, Thu · Afternoon · In person")
    expect(afternoons).toHaveTextContent("“School pickup in the mornings”")
    expect(afternoons).toHaveTextContent("You said, October 2026")

    expect(screen.getByTestId("preference-pref-2")).toHaveTextContent("You said, March 2026")
    expect(screen.getByTestId("preference-pref-3")).toHaveTextContent(
      "Interpreter: Needs a Spanish interpreter",
    )
    // A kind this screen has no editor for is read-only.
    expect(
      within(screen.getByTestId("preference-pref-3")).queryByRole("button"),
    ).not.toBeInTheDocument()
  })

  it("says so when there is nothing yet", () => {
    renderCard([])
    expect(screen.getByText("Nothing added yet.")).toBeInTheDocument()
  })

  it("removes a row by retiring it", async () => {
    renderCard()
    fireEvent.click(within(screen.getByTestId("preference-pref-2")).getByRole("button", { name: "Remove" }))
    await waitFor(() =>
      expect(retirePreference).toHaveBeenCalledWith({ patientId: "p-maya", preferenceId: "pref-2" }),
    )
  })

  it("adds times that don't work, always firm", async () => {
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    const dialog = screen.getByTestId("preference-dialog")
    expect(dialog).toHaveTextContent("For practical things. Clinical details belong in a note.")

    // One of each single kind is already on the chart, so only the limit and communication remain.
    expect(within(dialog).queryByRole("radio", { name: "Times that suit" })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole("radio", { name: "Practical" })).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole("radio", { name: "Times that don't work" }))

    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(within(dialog).getByText("Choose at least one day.")).toBeInTheDocument()
    expect(addPreference).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Fri" }))
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Mon" }))
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Morning" }))
    fireEvent.change(within(dialog).getByLabelText("In your words (optional)"), {
      target: { value: " Work " },
    })
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(addPreference).toHaveBeenCalledWith({
        patientId: "p-maya",
        data: {
          kind: "scheduling.hard_limit",
          value: { days: ["fri", "mon"], times: ["morning"], modality: "either" },
          text: "Work",
          strength: "hard",
        },
      }),
    )
  })

  it("asks for words on a note", async () => {
    renderCard([AFTERNOONS])
    fireEvent.click(screen.getByRole("button", { name: "Add" }))
    const dialog = screen.getByTestId("preference-dialog")
    fireEvent.click(within(dialog).getByRole("radio", { name: "Communication" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(within(dialog).getByText("Add a few words.")).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText("Note"), {
      target: { value: "Texts back faster than email" },
    })
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(addPreference).toHaveBeenCalledWith({
        patientId: "p-maya",
        data: { kind: "communication.note", text: "Texts back faster than email" },
      }),
    )
  })

  it("edits a row in place, firmness included", async () => {
    renderCard()
    fireEvent.click(within(screen.getByTestId("preference-pref-1")).getByRole("button", { name: "Edit" }))
    const dialog = screen.getByTestId("preference-dialog")
    expect(within(dialog).queryByRole("radio", { name: "Practical" })).not.toBeInTheDocument()
    expect(within(dialog).getByRole("checkbox", { name: "Thu" })).toBeChecked()

    fireEvent.click(within(dialog).getByRole("radio", { name: "Only these times" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(updatePreference).toHaveBeenCalledWith({
        patientId: "p-maya",
        preferenceId: "pref-1",
        data: {
          value: { days: ["mon", "thu"], times: ["afternoon"], modality: "in_person" },
          text: "School pickup in the mornings",
          strength: "hard",
        },
      }),
    )
  })
})
