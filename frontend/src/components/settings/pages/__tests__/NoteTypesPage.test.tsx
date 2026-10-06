// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Note types: listing, retiring and editing the practice's own
 * types, starting one from a template, and trying a draft before saving.
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
import type { NoteTypeSchema, PracticeNoteTypeSpec } from "@/types/noteTypes"
import template from "../../noteTypes/templates/psychiatric_follow_up.json"

const mockList = vi.fn()
const mockGet = vi.fn()
const mockSave = vi.fn()
const mockRetire = vi.fn()
const mockPreview = vi.fn()
const mockSessions = vi.fn()

vi.mock("@/lib/api/noteTypes", () => ({
  listNoteTypes: (...a: unknown[]) => mockList(...a),
  getNoteType: (...a: unknown[]) => mockGet(...a),
  savePracticeNoteType: (...a: unknown[]) => mockSave(...a),
  retirePracticeNoteType: (...a: unknown[]) => mockRetire(...a),
  previewNoteDraft: (...a: unknown[]) => mockPreview(...a),
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
