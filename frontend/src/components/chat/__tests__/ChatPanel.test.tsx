// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for ChatPanel baseline (THERAPY-q3z).
 * Mocks the SSE consumer + lifecycle API wrappers so we can drive the
 * panel deterministically without a backend.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest"
import {
  act,
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { ChatPanel } from "../ChatPanel"
import type {
  ChatStreamCallbacks,
  ChatStreamErrorEvent,
  ContextManifest,
} from "@/lib/chat/types"

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/chat/api", () => ({
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  updateConversation: vi.fn(),
  // BriefingCard fires this on mount in the empty state. Resolve with
  // an empty included list so the briefing falls through to the
  // neutral "ready to chat about Maria" copy — the lifecycle tests
  // below don't care about briefing content.
  previewChatContext: vi.fn().mockResolvedValue({
    manifest: {
      sources_included: [],
      sources_dropped: [],
      total_tokens_est: 0,
      token_budget: 600_000,
      patient_id: "patient-1",
      assembled_at: "2026-05-13T21:00:00Z",
    },
  }),
}))

vi.mock("@/lib/chat/sse", () => ({
  streamChatMessages: vi.fn(),
}))

// BriefingCard reads patient.first_name via usePatient. Mock to a fixed
// name so the panel tests don't need Firebase auth context.
vi.mock("@/hooks/usePatients", () => ({
  usePatient: () => ({ data: { first_name: "Maria" }, isLoading: false }),
}))

import {
  createConversation,
  getConversation,
  updateConversation,
} from "@/lib/chat/api"
import { streamChatMessages } from "@/lib/chat/sse"

const mockCreate = createConversation as unknown as Mock
const mockGet = getConversation as unknown as Mock
const mockUpdate = updateConversation as unknown as Mock
const mockStream = streamChatMessages as unknown as Mock

// Wrap render() with a QueryClientProvider because BriefingCard uses
// react-query for the manifest preview. A fresh client per render
// avoids cache bleed between tests.
function render(ui: React.ReactElement): ReturnType<typeof rtlRender> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return rtlRender(
    <QueryClientProvider client={qc}>{ui}</QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const NOW = "2026-05-13T21:00:00Z"
const CONV_ID = "conv-abc"

function buildManifest(): ContextManifest {
  return {
    sources_included: [
      {
        source_key: "progress_notes_recent",
        tokens_est: 1250,
        row_count: 3,
        note_ids: ["note-1", "note-2", "note-3"],
      },
      {
        source_key: "current_medications",
        tokens_est: 120,
        row_count: 1,
        note_ids: ["med-1"],
      },
    ],
    sources_dropped: [
      { source_key: "lab_values_recent", reason: "module_not_available" },
      { source_key: "vitals_recent", reason: "module_not_available" },
    ],
    total_tokens_est: 1370,
    token_budget: 600_000,
    patient_id: "patient-1",
    assembled_at: NOW,
  }
}

function happyStreamImpl(deltas: string[] = ["Hello, ", "this is ", "a reply."]) {
  return (
    _conversationId: string,
    _body: unknown,
    callbacks: ChatStreamCallbacks,
  ) => {
    callbacks.onMeta({
      user_message_id: "user-1",
      assistant_message_id: "assistant-1",
      input_tokens: 1370,
      model: "gemini-2.5-flash-lite",
      manifest: buildManifest(),
    })
    for (const text of deltas) {
      callbacks.onDelta({ text })
    }
    callbacks.onDone({ output_tokens: 32, finish_reason: "stop" })
    return Promise.resolve()
  }
}

function errorStreamImpl(error: ChatStreamErrorEvent) {
  return (
    _conversationId: string,
    _body: unknown,
    callbacks: ChatStreamCallbacks,
  ) => {
    callbacks.onError(error)
    return Promise.resolve()
  }
}

function defaultProps() {
  return {
    patientId: "patient-1",
    callerFeatureKey: "session_prep",
    callerSystemPrompt: "You are an assistant.",
    defaultSourceSelection: {
      progress_notes_recent: { limit: 3 } as const,
      patient_documents: true as const,
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockCreate.mockResolvedValue({
    id: CONV_ID,
    patient_id: "patient-1",
    owner_user_id: "user-x",
    title: "Chat about Maria",
    caller_feature_key: "session_prep",
    default_source_selection: {
      progress_notes_recent: { limit: 3 },
      current_medications: true,
    },
    created_at: NOW,
    last_turn_at: null,
    archived_at: null,
  })
  mockUpdate.mockImplementation((_id: string, body: { archive?: boolean }) =>
    Promise.resolve({
      id: CONV_ID,
      patient_id: "patient-1",
      owner_user_id: "user-x",
      title: "Chat about Maria",
      caller_feature_key: "session_prep",
      default_source_selection: {
        progress_notes_recent: { limit: 3 },
        current_medications: true,
      },
      created_at: NOW,
      last_turn_at: NOW,
      archived_at: body.archive ? NOW : null,
    }),
  )
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChatPanel — chip rail", () => {
  it("renders one chip per source in the default selection", () => {
    render(<ChatPanel {...defaultProps()} />)
    expect(screen.getByText("Progress notes")).toBeInTheDocument()
    expect(screen.getByText("Uploaded documents")).toBeInTheDocument()
    const rail = screen
      .getByText("Progress notes")
      .closest("[data-slot=chat-source-rail]")
    expect(rail).not.toBeNull()
  })

  it("toggling a chip removes the key from the next send's selection", async () => {
    mockStream.mockImplementation(happyStreamImpl())
    render(<ChatPanel {...defaultProps()} />)

    // Click the Uploaded documents chip body (the toggle button) to
    // deactivate it. The chip has two buttons (toggle + details); use
    // aria-pressed to pick the toggle.
    const docsToggle = screen
      .getAllByRole("button", { pressed: true })
      .find((el) => el.textContent?.startsWith("Uploaded documents"))
    expect(docsToggle).toBeDefined()
    fireEvent.click(docsToggle!)

    // Send a message.
    const textarea = screen.getByRole("textbox", { name: "Message" })
    fireEvent.change(textarea, { target: { value: "Hello there" } })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() => expect(mockStream).toHaveBeenCalled())
    const [, body] = mockStream.mock.calls[0]
    expect(body.content).toBe("Hello there")
    expect(body.source_selection).not.toHaveProperty("patient_documents")
    expect(body.source_selection).toHaveProperty("progress_notes_recent")
  })
})

describe("ChatPanel — happy-path stream", () => {
  it("creates a conversation lazily on first send and streams to a bubble", async () => {
    mockStream.mockImplementation(happyStreamImpl())
    render(<ChatPanel {...defaultProps()} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "Summarize Maria's last few sessions" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1))
    expect(mockCreate.mock.calls[0][0]).toMatchObject({
      patient_id: "patient-1",
      caller_feature_key: "session_prep",
      caller_system_prompt: "You are an assistant.",
    })

    await waitFor(() =>
      expect(
        screen.getByText("Summarize Maria's last few sessions"),
      ).toBeInTheDocument(),
    )
    await waitFor(() =>
      expect(screen.getByText("Hello, this is a reply.")).toBeInTheDocument(),
    )
  })

  it("expands the per-message manifest disclosure under the assistant reply", async () => {
    mockStream.mockImplementation(happyStreamImpl())
    render(<ChatPanel {...defaultProps()} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "hi" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() =>
      expect(screen.getByText("Hello, this is a reply.")).toBeInTheDocument(),
    )

    // The summary line lives in the manifest disclosure button. Verify
    // it appears and that clicking expands the per-source list.
    const summary = await screen.findByRole("button", { name: /Based on/i })
    expect(summary).toBeInTheDocument()
    fireEvent.click(summary)

    // The chip rail also shows "3 items" as a secondary label, so
    // scope to the manifest disclosure by looking for a note-id link
    // (which only appears in the expansion).
    expect(await screen.findByText("note-1")).toBeInTheDocument()
  })
})

describe("ChatPanel — error states", () => {
  it("renders the context_too_large notice with a 'Reset to defaults' remedy", async () => {
    mockStream.mockImplementation(
      errorStreamImpl({ error: "context_too_large", message: "too big" }),
    )
    render(<ChatPanel {...defaultProps()} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "explain" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveAttribute(
        "data-error-code",
        "context_too_large",
      ),
    )
    expect(
      screen.getByRole("button", { name: /Reset to defaults/i }),
    ).toBeInTheDocument()
  })

  it("renders the llm_error notice with a working 'Retry' that re-invokes the stream", async () => {
    mockStream
      .mockImplementationOnce(errorStreamImpl({ error: "llm_error", message: "boom" }))
      .mockImplementationOnce(happyStreamImpl(["Retried reply."]))
    render(<ChatPanel {...defaultProps()} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "go" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveAttribute("data-error-code", "llm_error")

    const retry = within(alert).getByRole("button", { name: "Retry" })
    await act(async () => {
      fireEvent.click(retry)
    })

    await waitFor(() => expect(mockStream).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getByText("Retried reply.")).toBeInTheDocument(),
    )
  })

  it("renders the safety_block notice with no remedy button", async () => {
    mockStream.mockImplementation(
      errorStreamImpl({ error: "safety_block", message: "blocked" }),
    )
    render(<ChatPanel {...defaultProps()} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "x" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveAttribute("data-error-code", "safety_block")
    expect(within(alert).queryByRole("button")).toBeNull()
  })
})

describe("ChatPanel — archive", () => {
  it("PATCHes archive=true on confirm and renders the archived footer", async () => {
    mockGet.mockResolvedValue({
      id: CONV_ID,
      patient_id: "patient-1",
      owner_user_id: "user-x",
      title: "Existing conversation",
      caller_feature_key: "session_prep",
      default_source_selection: { progress_notes_recent: { limit: 3 } },
      created_at: NOW,
      last_turn_at: NOW,
      archived_at: null,
      messages: [],
    })
    const onArchived = vi.fn()
    render(
      <ChatPanel
        {...defaultProps()}
        conversationId={CONV_ID}
        onArchived={onArchived}
      />,
    )

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1))
    fireEvent.click(
      await screen.findByRole("button", { name: "Archive conversation" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Archive" }))

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith(CONV_ID, { archive: true }),
    )
    await waitFor(() =>
      expect(
        screen.getByText("This conversation is archived."),
      ).toBeInTheDocument(),
    )
    expect(onArchived).toHaveBeenCalledWith(CONV_ID)
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull()
  })
})
