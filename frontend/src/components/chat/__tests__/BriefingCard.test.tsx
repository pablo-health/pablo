// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BriefingCard (§13.4) — sage-tinted Fraunces-italic empty state that
 * composes a lay-language sentence from the backend's manifest preview.
 *
 * The card is intentionally dumb about source semantics: everything it
 * says comes from ``manifest.sources_included`` (key + row_count +
 * latest_at). The FE never has to know which note_type backs a given
 * source — that mapping lives once, in the bundler.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { BriefingCard } from "../BriefingCard"
import type {
  ContextManifest,
  ManifestIncludedEntry,
  SourceSelection,
} from "@/lib/chat/types"

vi.mock("@/hooks/usePatients", () => ({
  usePatient: vi.fn(),
}))
vi.mock("@/lib/chat/api", () => ({
  previewChatContext: vi.fn(),
}))

import { usePatient } from "@/hooks/usePatients"
import { previewChatContext } from "@/lib/chat/api"

const mockUsePatient = usePatient as unknown as ReturnType<typeof vi.fn>
const mockPreview = previewChatContext as unknown as ReturnType<typeof vi.fn>

function makeManifest(
  sources: ManifestIncludedEntry[],
): ContextManifest {
  return {
    sources_included: sources,
    sources_dropped: [],
    total_tokens_est: 0,
    token_budget: 600_000,
    patient_id: "patient-1",
    assembled_at: "2026-05-14T00:00:00Z",
  }
}

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function renderCard(props: {
  firstName?: string | null
  selection?: SourceSelection
  manifest?: ContextManifest
}) {
  mockUsePatient.mockReturnValue({
    data: props.firstName ? { first_name: props.firstName } : undefined,
    isLoading: !props.firstName,
  })
  mockPreview.mockResolvedValue({
    manifest: props.manifest ?? makeManifest([]),
  })
  return render(
    wrap(
      <BriefingCard
        patientId="patient-1"
        selection={props.selection ?? {}}
      />,
    ),
  )
}

/**
 * Read the briefing sentence node, waiting for useQuery to resolve.
 * Uses findByRole to dodge text-matcher races: the sentence's text
 * can flip from "I'm ready…" (pre-data) to "I'm reading…" (post-data)
 * on a single render flush.
 */
async function getBriefingSentence(): Promise<HTMLElement> {
  return waitFor(() => {
    const node = document.querySelector(
      "[data-slot='chat-briefing-sentence']",
    )
    expect(node).not.toBeNull()
    return node as HTMLElement
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("BriefingCard", () => {
  it("composes the full pattern from the manifest's sources_included + latest_at", async () => {
    renderCard({
      firstName: "Maria",
      selection: {
        most_recent_intake: true,
        treatment_plan_active: true,
        safety_plan_active: true,
        progress_notes_recent: { limit: 5 },
      },
      manifest: makeManifest([
        {
          source_key: "most_recent_intake",
          tokens_est: 200,
          row_count: 1,
          latest_at: "2026-03-03T10:00:00Z",
        },
        {
          source_key: "treatment_plan_active",
          tokens_est: 100,
          row_count: 1,
          latest_at: "2026-04-01T10:00:00Z",
        },
        {
          source_key: "safety_plan_active",
          tokens_est: 100,
          row_count: 1,
          latest_at: "2026-04-15T10:00:00Z",
        },
        {
          source_key: "progress_notes_recent",
          tokens_est: 800,
          row_count: 3,
          latest_at: "2026-05-09T10:00:00Z",
        },
      ]),
    })
    await waitFor(() => {
      expect(
        document.querySelector("[data-slot='chat-briefing-sentence']")
          ?.textContent,
      ).toMatch(/^I'm reading Maria's/)
    })
    const sentence = await getBriefingSentence()
    expect(sentence.textContent).toMatch(
      /Maria's most recent intake from March 3/,
    )
    expect(sentence.textContent).toMatch(/the active treatment plan/)
    expect(sentence.textContent).toMatch(/the active safety plan/)
    expect(sentence.textContent).toMatch(
      /3 most recent progress notes \(last from May 9\)/,
    )
    expect(sentence.textContent).toMatch(/, and 3 most recent progress notes/)
  })

  it("omits sources the manifest reports with row_count: 0", async () => {
    renderCard({
      firstName: "Maria",
      selection: {
        most_recent_intake: true,
        treatment_plan_active: true,
        safety_plan_active: true,
      },
      manifest: makeManifest([
        {
          source_key: "most_recent_intake",
          tokens_est: 200,
          row_count: 1,
          latest_at: "2026-03-03T10:00:00Z",
        },
        {
          source_key: "treatment_plan_active",
          tokens_est: 100,
          row_count: 1,
          latest_at: "2026-04-01T10:00:00Z",
        },
        {
          source_key: "safety_plan_active",
          tokens_est: 0,
          row_count: 0,
        },
      ]),
    })
    await waitFor(() => {
      const text = document.querySelector(
        "[data-slot='chat-briefing-sentence']",
      )?.textContent
      expect(text).toMatch(/Maria's most recent intake from March 3/)
    })
    const sentence = await getBriefingSentence()
    expect(sentence.textContent).toMatch(/the active treatment plan/)
    expect(sentence.textContent).not.toMatch(/safety plan/i)
  })

  it("clamps progress-notes count by the manifest's row_count when fewer exist than the limit", async () => {
    renderCard({
      firstName: "Sam",
      selection: { progress_notes_recent: { limit: 10 } },
      manifest: makeManifest([
        {
          source_key: "progress_notes_recent",
          tokens_est: 300,
          row_count: 1,
          latest_at: "2026-05-09T10:00:00Z",
        },
      ]),
    })
    await waitFor(() => {
      expect(
        document.querySelector("[data-slot='chat-briefing-sentence']")
          ?.textContent,
      ).toMatch(/1 most recent progress note \(last from May 9\)/)
    })
  })

  it("falls back to a neutral invitation when no sources resolve to content", async () => {
    renderCard({
      firstName: "Alex",
      selection: { most_recent_intake: true },
      manifest: makeManifest([
        { source_key: "most_recent_intake", tokens_est: 0, row_count: 0 },
      ]),
    })
    await waitFor(() => {
      expect(
        document.querySelector("[data-slot='chat-briefing-sentence']")
          ?.textContent,
      ).toMatch(/I'm ready to chat about Alex\./)
    })
  })

  it("uses a generic stand-in when the patient's first name hasn't loaded yet", async () => {
    renderCard({
      firstName: null,
      selection: {},
      manifest: makeManifest([]),
    })
    await waitFor(() => {
      expect(
        document.querySelector("[data-slot='chat-briefing-sentence']")
          ?.textContent,
      ).toMatch(/I'm ready to chat about this patient\./)
    })
  })

  it("always renders the 'Ask me anything.' invitation line", async () => {
    renderCard({
      firstName: "Maria",
      selection: {},
      manifest: makeManifest([]),
    })
    expect(screen.getByText("Ask me anything.")).toBeInTheDocument()
  })

  it("does not mention pasted_text — the user already knows what they pasted in", async () => {
    renderCard({
      firstName: "Maria",
      selection: { pasted_text: { content: "long paste" } },
      manifest: makeManifest([
        {
          source_key: "pasted_text",
          tokens_est: 100,
          chars: 42,
        },
      ]),
    })
    await waitFor(() => {
      expect(
        document.querySelector("[data-slot='chat-briefing-sentence']")
          ?.textContent,
      ).toMatch(/I'm ready to chat about Maria\./)
    })
    expect(screen.queryByText(/pasted/i)).toBeNull()
  })

  it("calls previewChatContext with the patient_id and selection passed in", async () => {
    renderCard({
      firstName: "Maria",
      selection: { most_recent_intake: true, current_medications: true },
      manifest: makeManifest([]),
    })
    await waitFor(() => {
      expect(mockPreview).toHaveBeenCalledWith({
        patient_id: "patient-1",
        source_selection: {
          most_recent_intake: true,
          current_medications: true,
        },
      })
    })
  })

  it("uses the sage-tinted card surface (data-slot hook + secondary palette)", async () => {
    const { container } = renderCard({
      firstName: "Maria",
      selection: {},
      manifest: makeManifest([]),
    })
    const card = container.querySelector("[data-slot='chat-briefing-card']")
    expect(card).not.toBeNull()
    expect(card?.className).toMatch(/secondary-50/)
    const sentence = container.querySelector(
      "[data-slot='chat-briefing-sentence']",
    )
    expect(sentence?.className).toMatch(/font-display/)
    expect(sentence?.className).toMatch(/italic/)
  })
})
