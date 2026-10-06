// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Note types: listing, retiring and editing the practice's own
 * types, starting one from a template or from the clinician's own notes, and
 * trying a draft before saving.
 *
 * The API layer is mocked at its functions, so each test pins what the page
 * sends — above all that a template saved untouched is the template file, and
 * that trying a draft calls only the preview route.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { NoteTypesPage } from "../NoteTypesPage"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ApiError } from "@/lib/api/client"
import type { DeriveNoteTypeResponse, NoteTypeSchema, PracticeNoteTypeSpec } from "@/types/noteTypes"
import template from "../../noteTypes/templates/psychiatric_follow_up.json"

const mockList = vi.fn()
const mockGet = vi.fn()
const mockSave = vi.fn()
const mockRetire = vi.fn()
const mockPreview = vi.fn()
const mockSessions = vi.fn()
const mockDerive = vi.fn()
const mockReferences = vi.fn()

vi.mock("@/lib/api/noteTypes", () => ({
  listNoteTypes: (...a: unknown[]) => mockList(...a),
  getNoteType: (...a: unknown[]) => mockGet(...a),
  savePracticeNoteType: (...a: unknown[]) => mockSave(...a),
  retirePracticeNoteType: (...a: unknown[]) => mockRetire(...a),
  previewNoteDraft: (...a: unknown[]) => mockPreview(...a),
  deriveNoteType: (...a: unknown[]) => mockDerive(...a),
  listDeriveReferences: (...a: unknown[]) => mockReferences(...a),
}))

vi.mock("@/hooks/useSessions", () => ({
  useSessionList: () => ({ data: mockSessions() }),
}))

const COACH_SPEC: PracticeNoteTypeSpec = {
  label: "Coaching check-in",
  description: "A short check-in.",
  system_prompt: "You are a coaching assistant.",
  user_template: "{fields}\n\n{transcript}",
  sections: [
    {
      key: "summary",
      label: "Summary",
      fields: [{ key: "what_changed", label: "What changed", kind: "text", ai_hint: "Since last time." }],
    },
  ],
  inputs: [],
}

function schema(key: string, spec: PracticeNoteTypeSpec, version: number | null): NoteTypeSchema {
  return {
    key,
    label: spec.label,
    description: spec.description,
    tier: "core",
    context: "session",
    version,
    sections: spec.sections,
    inputs: spec.inputs,
  }
}

const SOAP = schema("soap", { ...COACH_SPEC, label: "SOAP" }, null)
const COACH = schema("custom.coach", COACH_SPEC, 3)

const TEMPLATE_SPEC = template.spec as PracticeNoteTypeSpec

beforeEach(() => {
  mockList.mockResolvedValue({ note_types: [SOAP, COACH] })
  mockGet.mockResolvedValue({ ...COACH, spec: COACH_SPEC })
  mockSave.mockImplementation((slug: string, spec: PracticeNoteTypeSpec) =>
    Promise.resolve(schema(`custom.${slug}`, spec, 4)),
  )
  mockRetire.mockResolvedValue(COACH)
  mockSessions.mockReturnValue({ data: [] })
  mockReferences.mockResolvedValue({ references: [] })
})

afterEach(() => vi.clearAllMocks())

describe("NoteTypesPage list", () => {
  it("lists only the practice's own types, with their version", async () => {
    renderWithProviders(<NoteTypesPage />)

    expect(await screen.findByText("Coaching check-in")).toBeInTheDocument()
    expect(screen.getByText(/Version 3/)).toBeInTheDocument()
    expect(screen.queryByText("SOAP")).not.toBeInTheDocument()
  })

  it("retires a type after asking", async () => {
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Retire Coaching check-in" }))
    expect(mockRetire).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Retire" }))

    await waitFor(() => expect(mockRetire).toHaveBeenCalledWith("coach", undefined))
  })
})

describe("NoteTypesPage editor", () => {
  it("saves an edit as the same slug, keeping the prompts it was loaded with", async () => {
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Edit Coaching check-in" }))
    const name = await screen.findByLabelText("Note type name")
    await user.clear(name)
    await user.type(name, "Coaching follow-up")
    await user.click(screen.getByRole("button", { name: "Add field" }))
    const added = within(screen.getByRole("group", { name: "Field 2" }))
    await user.type(added.getByLabelText("Field name"), "Next step")
    await user.selectOptions(added.getByLabelText("Shape"), "list")
    await user.click(screen.getByRole("button", { name: "Save note type" }))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    expect(mockSave).toHaveBeenCalledWith(
      "coach",
      {
        ...COACH_SPEC,
        label: "Coaching follow-up",
        sections: [
          {
            ...COACH_SPEC.sections[0],
            fields: [
              ...COACH_SPEC.sections[0].fields,
              { key: "next_step", label: "Next step", kind: "list", ai_hint: "" },
            ],
          },
        ],
      },
      undefined,
    )
    expect(await screen.findByRole("status")).toHaveTextContent("Saved Coaching follow-up, version 4.")
  })

  it("shows the server's message next to the field it names", async () => {
    mockSave.mockRejectedValue(
      new ApiError("UNKNOWN_ERROR", "API request failed with status 422", {
        validation: [
          { loc: ["body", "sections", 0, "fields", 0, "label"], msg: "String should have at most 80 characters" },
        ],
      }, 422),
    )
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Edit Coaching check-in" }))
    await screen.findByLabelText("Note type name")
    await user.click(screen.getByRole("button", { name: "Save note type" }))

    const field = within(screen.getByRole("group", { name: "Field 1" })).getByLabelText("Field name")
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"))
    expect(field).toHaveAccessibleDescription("String should have at most 80 characters")
    expect(screen.getByRole("alert")).toHaveTextContent("Fix the highlighted parts")
  })

  it("starting from the follow-up template and saving stores the template unchanged", async () => {
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(
      await screen.findByRole("button", { name: "Start from Psychiatric follow-up (E/M + psychotherapy)" }),
    )
    await user.click(screen.getByRole("button", { name: "Save note type" }))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    expect(mockSave).toHaveBeenCalledWith("psychiatric_follow_up", TEMPLATE_SPEC, undefined)
  })

  it("opens imported JSON in the editor, and refuses what isn't a note type", async () => {
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByText("Import JSON"))
    const box = screen.getByLabelText("Note type JSON")
    await user.click(box)
    await user.paste("{not json")
    await user.click(screen.getByRole("button", { name: "Open in editor" }))
    expect(screen.getByText("That isn't valid JSON.")).toBeInTheDocument()

    await user.clear(box)
    await user.click(box)
    await user.paste(JSON.stringify(COACH_SPEC))
    await user.click(screen.getByRole("button", { name: "Open in editor" }))
    expect(await screen.findByLabelText("Note type name")).toHaveValue("Coaching check-in")
  })
})

describe("NoteTypesPage try it", () => {
  it("drafts from the template's sample visit without saving anything", async () => {
    mockPreview.mockResolvedValue({
      key: "custom.preview",
      version: null,
      sections: { subjective: { chief_complaint: "Stand-in draft for subjective.chief_complaint." } },
    })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(
      await screen.findByRole("button", { name: "Start from Psychiatric follow-up (E/M + psychotherapy)" }),
    )
    await user.selectOptions(screen.getByLabelText(/^Place of service/), "Telehealth")
    await user.click(screen.getByRole("button", { name: "Draft a note" }))

    const draft = await screen.findByTestId("try-it-draft")
    expect(within(draft).getByText("Stand-in draft for subjective.chief_complaint.")).toBeInTheDocument()
    expect(mockPreview).toHaveBeenCalledWith(
      {
        spec: TEMPLATE_SPEC,
        transcript: { format: "txt", content: template.samples[0].transcript },
        inputs: { place_of_service: "Telehealth" },
      },
      undefined,
    )
    expect(mockSave).not.toHaveBeenCalled()
  })

  it("drafts from pasted text", async () => {
    mockPreview.mockResolvedValue({ key: "custom.preview", version: null, sections: {} })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "New note type" }))
    await user.type(screen.getByLabelText("Note type name"), "Brief")
    await user.type(screen.getByLabelText("Section name"), "Summary")
    await user.type(screen.getByLabelText("Field name"), "What happened")
    await user.click(screen.getByLabelText("Transcript"))
    await user.paste("Therapist: Hello.")
    await user.click(screen.getByRole("button", { name: "Draft a note" }))

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    const body = mockPreview.mock.calls[0][0]
    expect(body.transcript).toEqual({ format: "txt", content: "Therapist: Hello." })
    expect(body.spec.sections).toEqual([
      { key: "summary", label: "Summary", fields: [{ key: "what_happened", label: "What happened", kind: "text", ai_hint: "" }] },
    ])
    expect(mockSave).not.toHaveBeenCalled()
  })

  it("drafts from one of the clinician's recorded sessions", async () => {
    mockSessions.mockReturnValue({
      data: [
        { id: "s-empty", patient_name: "Lee, Ana", session_date: "2026-09-01", transcript: { format: "txt", content: "" } },
        { id: "s-1", patient_name: "Doe, Sam", session_date: "2026-09-02", transcript: { format: "vtt", content: "WEBVTT\n\nhi" } },
      ],
    })
    mockPreview.mockResolvedValue({ key: "custom.preview", version: null, sections: {} })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Edit Coaching check-in" }))
    await screen.findByLabelText("Note type name")
    await user.click(screen.getByRole("radio", { name: "One of your sessions" }))
    const picker = screen.getByLabelText(/^Session/)
    expect(within(picker).queryByText(/Lee, Ana/)).not.toBeInTheDocument()
    await user.selectOptions(picker, "s-1")
    await user.click(screen.getByRole("button", { name: "Draft a note" }))

    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1))
    expect(mockPreview.mock.calls[0][0].transcript).toEqual({ format: "vtt", content: "WEBVTT\n\nhi" })
  })
})

// A synthetic sample; no real client's words.
const STRAY = "Brought a drawing from a weekend art class."
const SAMPLE = `Interval history: Sleeping better.\nFollow up: Four weeks.\n${STRAY}`

const DERIVED_SPEC: PracticeNoteTypeSpec = {
  label: "Follow-up visit",
  description: "A short follow-up note.",
  system_prompt: "Write in brief clinical prose.",
  user_template: null,
  sections: [
    {
      key: "interval",
      label: "Interval",
      fields: [{ key: "interval_history", label: "Interval history", kind: "text", ai_hint: "Changes since last visit." }],
    },
    {
      key: "plan",
      label: "Plan",
      fields: [{ key: "follow_up", label: "Follow up", kind: "text", ai_hint: "When they return." }],
    },
  ],
  inputs: [],
}

const DERIVED: DeriveNoteTypeResponse = {
  spec: DERIVED_SPEC,
  coverage: [{ sample: 0, passages: 3, unplaced: [STRAY], checked: true }],
  guard: [],
  reference: null,
  suggestions: [],
}

async function proposeFromPastedSample(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Start from your notes" }))
  await user.click(screen.getByLabelText("Note 1"))
  await user.paste(SAMPLE)
  await user.click(screen.getByRole("button", { name: "Propose a note type" }))
  return screen.findByLabelText("Note type name")
}

describe("NoteTypesPage from your notes", () => {
  it("opens the proposal in the editor and saves it, sending the pasted sample once", async () => {
    mockDerive.mockResolvedValue(DERIVED)
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    expect(await proposeFromPastedSample(user)).toHaveValue("Follow-up visit")
    expect(mockDerive).toHaveBeenCalledTimes(1)
    expect(mockDerive).toHaveBeenCalledWith(
      { samples: [SAMPLE], files: [], description: "", reference: null },
      undefined,
    )
    expect(mockSave).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Save note type" }))
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    expect(mockSave).toHaveBeenCalledWith("follow_up_visit", DERIVED_SPEC, undefined)
    expect(await screen.findByRole("status")).toHaveTextContent("Saved Follow-up visit, version 4.")
  })

  it("says the notes are not kept", async () => {
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Start from your notes" }))
    expect(screen.getByText(/Your notes are used for this and not kept\./)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Propose a note type" })).toBeDisabled()
  })

  it("shows a passage with no field, and 'Add a field for this' adds one to the editor", async () => {
    mockDerive.mockResolvedValue(DERIVED)
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)
    await proposeFromPastedSample(user)

    const passage = screen.getByTestId("unplaced-passage")
    expect(passage).toHaveTextContent(STRAY)
    await user.click(within(passage).getByRole("button", { name: "Add a field for this" }))

    expect(passage).toHaveTextContent("Field added to Plan")
    const plan = screen.getAllByRole("group", { name: "Section 2" })[0]
    const added = within(within(plan).getByRole("group", { name: "Field 2" })).getByLabelText("Field name")
    expect(added).toHaveValue("")
    expect(added).toHaveFocus()
    await user.type(added, "Other observations")
    await user.click(screen.getByRole("button", { name: "Save note type" }))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const saved = mockSave.mock.calls[0][1] as PracticeNoteTypeSpec
    // The passage itself never goes into the definition.
    expect(JSON.stringify(saved)).not.toContain(STRAY)
    expect(saved.sections[1].fields).toEqual([
      DERIVED_SPEC.sections[1].fields[0],
      { key: "other_observations", label: "Other observations", kind: "text", ai_hint: "" },
    ])
  })

  it("counts lines left out as not note content in one quiet line", async () => {
    mockDerive.mockResolvedValue({ ...DERIVED, coverage: [{ ...DERIVED.coverage[0], excluded: 16 }] })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)
    await proposeFromPastedSample(user)

    expect(screen.getByTestId("excluded-lines")).toHaveTextContent("16 lines left out as not part of the note.")
    expect(screen.getAllByTestId("unplaced-passage")).toHaveLength(1)
  })

  it("says nothing about left-out lines when there are none or the count is absent", async () => {
    mockDerive.mockResolvedValue({
      ...DERIVED,
      coverage: [{ ...DERIVED.coverage[0], excluded: 0 }, { sample: 1, passages: 2, unplaced: [], checked: true }],
    })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)
    await proposeFromPastedSample(user)

    expect(screen.queryByTestId("excluded-lines")).not.toBeInTheDocument()
  })

  it("says so when everything found a field", async () => {
    mockDerive.mockResolvedValue({ ...DERIVED, coverage: [{ sample: 0, passages: 2, unplaced: [], checked: true }] })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)
    await proposeFromPastedSample(user)

    expect(screen.getByText("Everything in your notes has a field.")).toBeInTheDocument()
    expect(screen.queryByTestId("unplaced-passage")).not.toBeInTheDocument()
  })

  it("compares with a chosen reference, adding or ignoring what it suggests", async () => {
    mockReferences.mockResolvedValue({ references: [{ key: "ref.follow_up", label: "Follow-up checklist" }] })
    mockDerive.mockResolvedValue({
      ...DERIVED,
      reference: { key: "ref.follow_up", label: "Follow-up checklist" },
      suggestions: [
        { label: "Risk", description: "Current risk and what was done about it." },
        { label: "Allergies", description: "" },
      ],
    })
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Start from your notes" }))
    await user.type(screen.getByLabelText(/^How you write your notes/), "Interval, then plan.")
    await user.selectOptions(screen.getByLabelText(/^Compare with/), await screen.findByRole("option", { name: "Follow-up checklist" }))
    await user.click(screen.getByRole("button", { name: "Propose a note type" }))
    await screen.findByLabelText("Note type name")
    expect(mockDerive).toHaveBeenCalledWith(
      { samples: [], files: [], description: "Interval, then plan.", reference: "ref.follow_up" },
      undefined,
    )

    const [risk, allergies] = screen.getAllByTestId("reference-suggestion")
    await user.click(within(allergies).getByRole("button", { name: "Ignore" }))
    expect(screen.getAllByTestId("reference-suggestion")).toHaveLength(1)
    await user.click(within(risk).getByRole("button", { name: "Add as a section" }))
    expect(risk).toHaveTextContent("Section added")
    expect(within(screen.getByRole("group", { name: "Section 3" })).getByLabelText("Section name")).toHaveValue("Risk")

    await user.click(screen.getByRole("button", { name: "Save note type" }))
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    expect((mockSave.mock.calls[0][1] as PracticeNoteTypeSpec).sections[2]).toEqual({
      key: "risk",
      label: "Risk",
      fields: [{ key: "risk", label: "Risk", kind: "text", ai_hint: "Current risk and what was done about it." }],
    })
  })

  it("shows the server's reason when nothing could be proposed", async () => {
    mockDerive.mockRejectedValue(
      new ApiError("UNPROCESSABLE", "A note type could not be proposed from these samples. Try again, or add a description.", undefined, 422),
    )
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Start from your notes" }))
    await user.click(screen.getByLabelText("Note 1"))
    await user.paste(SAMPLE)
    await user.click(screen.getByRole("button", { name: "Propose a note type" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be proposed from these samples")
    expect(screen.queryByLabelText("Note type name")).not.toBeInTheDocument()
  })

  it("takes at most three notes", async () => {
    const user = userEvent.setup()
    renderWithProviders(<NoteTypesPage />)

    await user.click(await screen.findByRole("button", { name: "Start from your notes" }))
    await user.click(screen.getByRole("button", { name: "Add another note" }))
    await user.click(screen.getByRole("button", { name: "Add another note" }))
    expect(screen.getByLabelText("Note 3")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add another note" })).not.toBeInTheDocument()

    for (const n of [1, 2, 3]) {
      await user.click(screen.getByLabelText(`Note ${n}`))
      await user.paste(SAMPLE)
    }
    await user.upload(screen.getByLabelText(/^Upload notes/), new File([SAMPLE], "note.txt", { type: "text/plain" }))
    expect(screen.getByText("Use up to 3 notes.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Propose a note type" })).toBeDisabled()
  })
})
