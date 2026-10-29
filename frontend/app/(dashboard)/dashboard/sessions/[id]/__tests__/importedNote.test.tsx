// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * An imported note on the session page, against the real page component.
 *
 * It is a reference copy of a note documented and signed where the visit
 * happened: labelled as imported with its date of service and, when the
 * document names one, its signer as written; read-only; never offered for
 * signing; and its chart updates are decided right here.
 */

import { Suspense } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, render, screen } from "@testing-library/react"
import SessionDetailPage from "../page"
import { createMockNote, createMockSession } from "@/test/factories"
import type { SessionStatus } from "@/types/sessions"

const { mockUseSession } = vi.hoisted(() => ({ mockUseSession: vi.fn() }))

vi.mock("@/hooks/useSessions", () => ({
  useSession: () => mockUseSession(),
  useUpdateSessionMetadata: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateSessionRating: () => ({ mutateAsync: vi.fn() }),
  useRedraftSessionNote: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))
vi.mock("@/hooks/useNotes", () => ({
  useUpdateNoteEdits: () => ({ mutateAsync: vi.fn(), isError: false, isPending: false }),
}))
vi.mock("@/hooks/useDictations", () => ({
  useSessionDictations: () => ({ data: { data: [] } }),
  useAddSessionDictation: () => ({ mutateAsync: vi.fn() }),
}))
vi.mock("@/hooks/useNoteTypes", () => ({ useNoteTypeLabel: () => (key: string) => key }))
vi.mock("@/hooks/useNoteSigning", () => ({ useNoteSigning: () => ({ data: undefined }) }))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "UTC" }))
vi.mock("@/hooks/useVisitTimes", () => ({ useVisitPdfLines: () => [] }))

vi.mock("@/components/sessions/NoteConsentLine", () => ({ NoteConsentLine: () => null }))
vi.mock("@/components/sessions/MdmReviewPanel", () => ({ MdmReviewPanel: () => null }))
vi.mock("@/components/sessions/VisitTimesPanel", () => ({ VisitTimesPanel: () => null }))
vi.mock("@/components/sessions/DictateMore", () => ({ DictateMore: () => <div>dictate</div> }))
vi.mock("@/components/sessions/SessionDetailHeader", () => ({ SessionDetailHeader: () => <div /> }))
vi.mock("@/components/sessions/TranscriptViewer", () => ({ TranscriptViewer: () => <div /> }))
vi.mock("@/components/sessions/QualityRating", () => ({ QualityRating: () => <div /> }))
vi.mock("@/components/sessions/QualityRatingWithFeedback", () => ({
  QualityRatingWithFeedback: () => <div />,
}))
vi.mock("@/components/payments/ChargeCardSection", () => ({ ChargeCardSection: () => <div /> }))

// Stand-ins that say what the page handed them.
vi.mock("@/components/sessions/NoteViewer", () => ({
  NoteViewer: ({ readonly }: { readonly: boolean }) => (
    <div>{readonly ? "note read-only" : "note editable"}</div>
  ),
}))
vi.mock("@/components/sessions/NoteInputsPanel", () => ({
  NoteInputsPanel: ({ editable }: { editable: boolean }) => (
    <div>{editable ? "details editable" : "details read-only"}</div>
  ),
}))
vi.mock("@/components/sessions/FinalizeButton", () => ({
  FinalizeButton: () => <button>Sign and lock</button>,
}))
vi.mock("@/components/notes/signing/NoteSignaturePanel", () => ({
  NoteSignaturePanel: () => <div>signature panel</div>,
}))
vi.mock("@/components/notes/chartUpdates/ChartUpdatesPanel", () => ({
  ChartUpdatesPanel: () => <div>chart updates</div>,
}))

async function renderPage() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <SessionDetailPage params={Promise.resolve({ id: "session-1" })} />
      </Suspense>,
    )
  })
}

function givenImport(status: SessionStatus, signer: string | null) {
  mockUseSession.mockReturnValue({
    data: createMockSession({
      status,
      source: "imported",
      session_date: "2026-08-19T14:30:00",
      source_signer: signer,
      source_document_id: "doc-1",
      note: createMockNote({ note_type: "custom.psychiatric_follow_up", finalized_at: null }),
    }),
    isLoading: false,
    error: null,
  })
}

afterEach(() => {
  vi.clearAllMocks()
})

describe("an imported note on the session page", () => {
  it("is labelled as imported, with its date of service and its signer as written", async () => {
    givenImport("imported", "A. Rivera, PMHNP-BC")
    await renderPage()

    const notice = screen.getByRole("region", { name: "Imported note" })
    expect(notice).toHaveTextContent("Imported")
    expect(notice).toHaveTextContent("Documented elsewhere for the visit on August 19, 2026.")
    expect(notice).toHaveTextContent("Signed by A. Rivera, PMHNP-BC")
  })

  it("names no signer when the document states none", async () => {
    givenImport("imported", null)
    await renderPage()

    expect(screen.getByRole("region", { name: "Imported note" })).not.toHaveTextContent(
      "Signed by",
    )
  })

  it("is read-only, offers no way to sign it, and decides its chart updates here", async () => {
    givenImport("imported", null)
    await renderPage()

    expect(screen.getByText("note read-only")).toBeInTheDocument()
    expect(screen.getByText("details read-only")).toBeInTheDocument()
    expect(screen.getByText("chart updates")).toBeInTheDocument()
    // The file it was read from, beside the text taken out of it.
    expect(screen.getByRole("button", { name: "Open original file" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Sign and lock" })).not.toBeInTheDocument()
    expect(screen.queryByText("signature panel")).not.toBeInTheDocument()
    expect(screen.queryByText("dictate")).not.toBeInTheDocument()
  })
})
