// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChatPanelWithHistory tests (PABLO-6x5.8 regression).
 *
 * The bug: the panel was keyed on the conversation id, so the lazy
 * create-on-first-send changed the key, React unmounted the in-flight
 * panel and remounted a fresh one that hydrated the still-empty new
 * conversation — losing the first message + its streamed reply.
 *
 * These tests render the real wrapper + sidebar in jsdom (a key change
 * really unmounts/remounts here) and assert the first message survives,
 * while a user-driven sidebar select still remounts and hydrates.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest"
import {
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { ChatPanelWithHistory } from "../ChatPanelWithHistory"
import type { ChatStreamCallbacks } from "@/lib/chat/types"

vi.mock("@/lib/chat/api", () => ({
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  updateConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
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

vi.mock("@/hooks/usePatients", () => ({
  usePatient: () => ({ data: { first_name: "Maria" }, isLoading: false }),
}))

import {
  createConversation,
  getConversation,
  listConversations,
} from "@/lib/chat/api"
import { streamChatMessages } from "@/lib/chat/sse"

const mockCreate = createConversation as unknown as Mock
const mockGet = getConversation as unknown as Mock
const mockList = listConversations as unknown as Mock
const mockStream = streamChatMessages as unknown as Mock

const NOW = "2026-05-13T21:00:00Z"
const NEW_ID = "conv-new"

function happyStreamImpl() {
  return (
    _conversationId: string,
    _body: unknown,
    callbacks: ChatStreamCallbacks,
  ) => {
    callbacks.onMeta({
      user_message_id: "user-1",
      assistant_message_id: "assistant-1",
      input_tokens: 10,
      model: "gemini-2.5-flash-lite",
      manifest: {
        sources_included: [],
        sources_dropped: [],
        total_tokens_est: 10,
        token_budget: 600_000,
        patient_id: "patient-1",
        assembled_at: NOW,
      },
    })
    callbacks.onDelta({ text: "Streamed reply." })
    callbacks.onDone({ output_tokens: 4, finish_reason: "stop" })
    return Promise.resolve()
  }
}

function render(ui: React.ReactElement): ReturnType<typeof rtlRender> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

function props() {
  return {
    patientId: "patient-1",
    callerFeatureKey: "session_prep",
    callerSystemPrompt: "You are an assistant.",
    defaultSourceSelection: {},
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue({ data: [], total: 0 })
  mockCreate.mockResolvedValue({
    id: NEW_ID,
    patient_id: "patient-1",
    owner_user_id: "user-x",
    title: "Chat about Maria",
    caller_feature_key: "session_prep",
    default_source_selection: {},
    created_at: NOW,
    last_turn_at: null,
    archived_at: null,
  })
})

describe("ChatPanelWithHistory — new-chat first message (PABLO-6x5.8)", () => {
  it("keeps the first message and its reply when the conversation is lazily created", async () => {
    mockStream.mockImplementation(happyStreamImpl())
    render(<ChatPanelWithHistory {...props()} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "Summarize Maria's last few sessions" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send" }))

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1))

    // The first user turn and the streamed reply both survive — the panel
    // was NOT remounted out from under the in-flight send.
    await waitFor(() =>
      expect(
        screen.getByText("Summarize Maria's last few sessions"),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText("Streamed reply.")).toBeInTheDocument()

    // The smoking gun: hydrating the just-created (empty) conversation is
    // exactly what the remount bug did. It must never fire here.
    expect(mockGet).not.toHaveBeenCalled()
    expect(screen.queryByText(/loading conversation/i)).not.toBeInTheDocument()
  })
})

describe("ChatPanelWithHistory — selecting an existing conversation", () => {
  it("remounts and hydrates the picked conversation", async () => {
    mockList.mockResolvedValue({
      data: [
        {
          id: "conv-old",
          patient_id: "patient-1",
          owner_user_id: "user-x",
          title: "Earlier chat",
          caller_feature_key: "session_prep",
          default_source_selection: {},
          created_at: NOW,
          last_turn_at: NOW,
          archived_at: null,
        },
      ],
      total: 1,
    })
    mockGet.mockResolvedValue({
      id: "conv-old",
      patient_id: "patient-1",
      owner_user_id: "user-x",
      title: "Earlier chat",
      caller_feature_key: "session_prep",
      default_source_selection: {},
      created_at: NOW,
      last_turn_at: NOW,
      archived_at: null,
      messages: [
        {
          id: "m1",
          role: "user",
          content: "Previously asked question",
          created_at: NOW,
          context_manifest: null,
        },
      ],
    })

    render(<ChatPanelWithHistory {...props()} />)

    const openRow = await screen.findByTestId("chat-history-open")
    fireEvent.click(openRow)

    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith("conv-old"),
    )
    await waitFor(() =>
      expect(screen.getByText("Previously asked question")).toBeInTheDocument(),
    )
  })
})
