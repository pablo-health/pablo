// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The signature panel under a note: what a signed, a legacy-finalized, an
 * unsigned and an unlocked-then-re-signed note each show, and which actions
 * each allows.
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NoteSignaturePanel } from "../signing/NoteSignaturePanel"
import { createMockNote } from "@/test/factories"
import type { NoteSignature, NoteSigningRecord } from "@/types/notes"

const { signing, mutations } = vi.hoisted(() => ({
  signing: { record: undefined as NoteSigningRecord | undefined },
  mutations: {
    sign: vi.fn(),
    unlock: vi.fn(),
    addendum: vi.fn(),
  },
}))

vi.mock("@/hooks/useNoteSigning", () => ({
  useNoteSigning: () => ({ data: signing.record }),
  useSignNote: () => ({ mutateAsync: mutations.sign }),
  useUnlockNote: () => ({ mutateAsync: mutations.unlock }),
  useAddNoteAddendum: () => ({ mutateAsync: mutations.addendum }),
  useSignerDefaults: () => ({ name: "Sam Ortiz", credentials: "LMFT" }),
}))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "America/New_York" }))
vi.mock("@/components/sessions/NoteViewer", () => ({
  NoteViewer: ({ note }: { note: { content_edited: unknown } }) => (
    <pre data-testid="version-body">{JSON.stringify(note.content_edited)}</pre>
  ),
}))

const SIGNED_AT = "2026-10-05T19:04:00Z"

function version(overrides: Partial<NoteSignature> = {}): NoteSignature {
  return {
    id: "sig-1",
    version: 1,
    signed_by: "user-1",
    signer_name: "Sam Ortiz",
    signer_credentials: "LMFT",
    signed_at: SIGNED_AT,
    unlocked_at: null,
    unlocked_by: null,
    unlock_reason: null,
    note_type: "soap",
    note_type_version: null,
    content: null,
    content_edited: null,
    ...overrides,
  }
}

function given(record: Partial<NoteSigningRecord>) {
  signing.record = {
    note_id: "note-1",
    finalized_at: null,
    signature: null,
    versions: [],
    addenda: [],
    ...record,
  }
}

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllEnvs()
  signing.record = undefined
})

const buttonNames = () =>
  screen.queryAllByRole("button").map((b) => b.textContent?.trim() ?? "")

describe("a signed note", () => {
  it("shows the block and offers only Add addendum and Unlock", () => {
    const current = version()
    given({ finalized_at: SIGNED_AT, signature: current, versions: [current] })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: SIGNED_AT })} canSign />)

    const block = screen.getByTestId("signature-block")
    expect(block).toHaveTextContent("Electronically signed by Sam Ortiz, LMFT")
    expect(block).toHaveTextContent("Oct 5, 2026, 3:04 PM EDT")
    expect(buttonNames()).toEqual(["Add addendum", "Unlock"])
    expect(screen.queryByText(/Signed versions/)).not.toBeInTheDocument()
  })

  it("lists addenda under the note, each with its own signature", () => {
    const current = version()
    given({
      finalized_at: SIGNED_AT,
      signature: current,
      versions: [current],
      addenda: [
        {
          id: "a-1",
          text: "Called after the session.",
          signer_name: "Sam Ortiz",
          signer_credentials: null,
          created_by: "user-1",
          created_at: "2026-10-05T20:00:00Z",
        },
      ],
    })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: SIGNED_AT })} />)

    const item = screen.getByText("Called after the session.").closest("li")!
    expect(item).toHaveTextContent("Electronically signed by Sam Ortiz")
    expect(item).toHaveTextContent("Oct 5, 2026, 4:00 PM EDT")
  })

  it("offers a dictation's draft addendum, prefilled, and signs it as the dictation's", async () => {
    const user = userEvent.setup()
    mutations.addendum.mockResolvedValue({})
    const current = version()
    given({ finalized_at: SIGNED_AT, signature: current, versions: [current] })
    render(
      <NoteSignaturePanel
        note={createMockNote({ finalized_at: SIGNED_AT })}
        draftAddendum={{ dictationId: "d-1", text: "Next session: two weeks from today." }}
      />,
    )

    const draft = screen.getByTestId("draft-addendum")
    expect(draft).toHaveTextContent("Next session: two weeks from today.")
    await user.click(within(draft).getByRole("button", { name: "Review and sign" }))
    const dialog = screen.getByRole("dialog")
    const text = within(dialog).getByLabelText("Addendum")
    expect(text).toHaveValue("Next session: two weeks from today.")
    await user.type(text, " Same time.")
    await user.type(within(dialog).getByLabelText("Your name"), "{selectall}Sam Ortiz")
    await user.click(within(dialog).getByRole("button", { name: "Sign and add" }))

    expect(mutations.addendum).toHaveBeenCalledWith({
      noteId: "note-1",
      data: expect.objectContaining({
        text: "Next session: two weeks from today. Same time.",
        dictation_id: "d-1",
      }),
    })
  })

  it("offers no draft addendum on an unsigned note", () => {
    given({})
    render(
      <NoteSignaturePanel
        note={createMockNote({ finalized_at: null })}
        canSign
        draftAddendum={{ dictationId: "d-1", text: "x" }}
      />,
    )
    expect(screen.queryByTestId("draft-addendum")).not.toBeInTheDocument()
  })

  it("unlocks only with a reason", async () => {
    const user = userEvent.setup()
    mutations.unlock.mockResolvedValue({})
    const current = version()
    given({ finalized_at: SIGNED_AT, signature: current, versions: [current] })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: SIGNED_AT })} />)

    await user.click(screen.getByRole("button", { name: /Unlock/ }))
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveTextContent("You are responsible for the changes you make")
    const accept = within(dialog).getByRole("button", { name: "Accept & unlock" })
    expect(accept).toBeDisabled()

    await user.type(within(dialog).getByLabelText("Reason for unlocking"), "  Wrong date  ")
    await user.click(accept)
    expect(mutations.unlock).toHaveBeenCalledWith({
      noteId: "note-1",
      data: { reason: "Wrong date" },
    })
  })

  it("adds an addendum with the signature as entered", async () => {
    const user = userEvent.setup()
    mutations.addendum.mockResolvedValue({})
    const current = version()
    given({ finalized_at: SIGNED_AT, signature: current, versions: [current] })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: SIGNED_AT })} />)

    await user.click(screen.getByRole("button", { name: /Add addendum/ }))
    const dialog = screen.getByRole("dialog")
    await user.type(within(dialog).getByLabelText("Addendum"), "Safety plan reviewed.")
    await user.clear(within(dialog).getByLabelText("Credentials"))
    await user.click(within(dialog).getByRole("button", { name: "Sign and add" }))
    expect(mutations.addendum).toHaveBeenCalledWith({
      noteId: "note-1",
      data: {
        text: "Safety plan reviewed.",
        signer_name: "Sam Ortiz",
        signer_credentials: null,
      },
    })
  })

  it("offers no actions in read-only mode", () => {
    vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")
    const current = version()
    given({ finalized_at: SIGNED_AT, signature: current, versions: [current] })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: SIGNED_AT })} />)
    expect(screen.getByTestId("signature-block")).toBeInTheDocument()
    expect(buttonNames()).toEqual([])
  })
})

describe("a note finalized before signatures", () => {
  it("shows Finalized and no signature line, and can be signed", () => {
    given({ finalized_at: "2024-01-15T14:30:00Z" })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: "2024-01-15T14:30:00Z" })} />)

    expect(screen.getByTestId("signature-block")).toHaveTextContent("Finalized Jan 15, 2024")
    expect(screen.queryByText(/Electronically signed/)).not.toBeInTheDocument()
    expect(buttonNames()).toEqual(["Add addendum", "Sign note"])
  })
})

describe("an unsigned note", () => {
  it("shows nothing where the page owns signing", () => {
    given({})
    const { container } = render(<NoteSignaturePanel note={createMockNote()} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe("a note unlocked and signed again", () => {
  const first = version({
    unlocked_at: "2026-10-06T14:00:00Z",
    unlocked_by: "user-1",
    unlock_reason: "Wrong date of service",
    content_edited: { plan: "first" },
  })
  const second = version({
    id: "sig-2",
    version: 2,
    signed_at: "2026-10-06T15:00:00Z",
    signer_credentials: "LMFT, LPCC",
  })

  it("lists the signed versions with the reason, each viewable", async () => {
    const user = userEvent.setup()
    given({ finalized_at: second.signed_at, signature: second, versions: [first, second] })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: second.signed_at })} />)

    expect(screen.getByTestId("signature-block")).toHaveTextContent(
      "Electronically signed by Sam Ortiz, LMFT, LPCC",
    )
    await user.click(screen.getByText("Signed versions (2)"))
    const rows = screen.getAllByRole("listitem")
    expect(rows[0]).toHaveTextContent("Version 1")
    expect(rows[0]).toHaveTextContent("Unlocked Oct 6, 2026, 10:00 AM EDT: Wrong date of service")
    await user.click(within(rows[0]).getByRole("button", { name: "View" }))
    expect(screen.getByTestId("version-body")).toHaveTextContent('{"plan":"first"}')
  })

  it("offers Sign and lock while unlocked where the page allows it", async () => {
    const user = userEvent.setup()
    mutations.sign.mockResolvedValue({})
    given({ versions: [first] })
    render(<NoteSignaturePanel note={createMockNote({ finalized_at: null })} canSign />)

    expect(screen.queryByTestId("signature-block")).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Sign and lock/ }))
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Sign and lock" }),
    )
    expect(mutations.sign).toHaveBeenCalledWith({
      noteId: "note-1",
      data: { signer_name: "Sam Ortiz", signer_credentials: "LMFT" },
    })
  })
})
