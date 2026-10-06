// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The body each signed version shows, through the real note viewer: a
 * superseded version shows what was signed then, edits included, not the
 * draft and not the note as it stands now.
 */

import { afterEach, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NoteSignaturePanel } from "../signing/NoteSignaturePanel"
import {
  createMockNote,
  createMockSOAPNote,
  createMockStructuredSOAPNote,
} from "@/test/factories"
import type { NoteSignature, NoteSigningRecord } from "@/types/notes"

const signing = vi.hoisted(() => ({ record: undefined as NoteSigningRecord | undefined }))

vi.mock("@/hooks/useNoteSigning", () => ({
  useNoteSigning: () => ({ data: signing.record }),
  useSignNote: () => ({ mutateAsync: vi.fn() }),
  useUnlockNote: () => ({ mutateAsync: vi.fn() }),
  useAddNoteAddendum: () => ({ mutateAsync: vi.fn() }),
  useSignerDefaults: () => ({ name: "Sam Ortiz", credentials: "LMFT" }),
}))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "America/New_York" }))
vi.mock("@/lib/config", () => ({ useConfig: () => ({ showVerificationBadges: true }) }))

afterEach(() => {
  signing.record = undefined
})

// Generation stores the four structured sections and no narrative beside them.
function draft(): Record<string, unknown> {
  const { narrative: _omitted, ...sections } = createMockStructuredSOAPNote()
  return sections as unknown as Record<string, unknown>
}

function version(overrides: Partial<NoteSignature>): NoteSignature {
  return {
    id: "sig-1",
    version: 1,
    signed_by: "user-1",
    signer_name: "Sam Ortiz",
    signer_credentials: "LMFT",
    signed_at: "2026-10-05T19:04:00Z",
    unlocked_at: null,
    unlocked_by: null,
    unlock_reason: null,
    note_type: "soap",
    note_type_version: null,
    content: draft(),
    content_edited: null,
    ...overrides,
  }
}

it("shows each superseded version's own edited body", async () => {
  const user = userEvent.setup()
  const first = version({
    unlocked_at: "2026-10-06T14:00:00Z",
    unlocked_by: "user-1",
    unlock_reason: "Wrong date of service",
    content_edited: { ...createMockSOAPNote({ plan: "Body signed first" }) },
  })
  const second = version({
    id: "sig-2",
    version: 2,
    signed_at: "2026-10-06T15:00:00Z",
    content_edited: { ...createMockSOAPNote({ plan: "Body signed second" }) },
  })
  signing.record = {
    note_id: "note-1",
    finalized_at: second.signed_at,
    signature: second,
    versions: [first, second],
    addenda: [],
  }
  const note = createMockNote({
    finalized_at: second.signed_at,
    content: draft(),
    content_edited: second.content_edited,
  })
  render(<NoteSignaturePanel note={note} />)

  await user.click(screen.getByText("Signed versions (2)"))
  const [firstRow, secondRow] = screen.getAllByRole("listitem")
  await user.click(within(firstRow).getByRole("button", { name: "View" }))
  await user.click(within(secondRow).getByRole("button", { name: "View" }))

  expect(within(firstRow).getByText("Body signed first")).toBeInTheDocument()
  expect(within(firstRow).queryByText("Body signed second")).not.toBeInTheDocument()
  expect(within(secondRow).getByText("Body signed second")).toBeInTheDocument()
  expect(screen.queryByText("Review progress next session")).not.toBeInTheDocument()
})

it("shows a version signed straight from the draft as the draft", async () => {
  const user = userEvent.setup()
  const only = version({})
  signing.record = {
    note_id: "note-1",
    finalized_at: null,
    signature: null,
    versions: [{ ...only, unlocked_at: "2026-10-06T14:00:00Z", unlock_reason: "Typo" }],
    addenda: [],
  }
  render(
    <NoteSignaturePanel
      note={createMockNote({ content: draft(), content_edited: { ...createMockSOAPNote() } })}
    />,
  )

  await user.click(screen.getByText("Signed versions (1)"))
  await user.click(screen.getByRole("button", { name: "View" }))
  expect(screen.getByText("Review progress next session")).toBeInTheDocument()
  expect(screen.getByText("AI Generated")).toBeInTheDocument()
})
