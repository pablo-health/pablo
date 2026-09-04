// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChatHistorySidebar tests.
 *
 * Renders the sidebar directly (no parent panel) against a mocked
 * ``@/lib/chat/api`` so the rename / archive / delete / show-archived
 * affordances can be exercised without a real backend.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest"
import {
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { ChatHistorySidebar } from "../ChatHistorySidebar"
import type { ChatConversation } from "@/lib/chat/types"

vi.mock("@/lib/chat/api", () => ({
  listConversations: vi.fn(),
  updateConversation: vi.fn(),
  deleteConversation: vi.fn(),
}))

import {
  deleteConversation,
  listConversations,
  updateConversation,
} from "@/lib/chat/api"

const mockList = listConversations as unknown as Mock
const mockUpdate = updateConversation as unknown as Mock
const mockDelete = deleteConversation as unknown as Mock

const NOW = "2026-05-13T21:00:00Z"

function conversation(overrides: Partial<ChatConversation>): ChatConversation {
  return {
    id: "conv-1",
    patient_id: "patient-1",
    owner_user_id: "user-x",
    title: "First chat",
    caller_feature_key: "session_prep",
    default_source_selection: {},
    created_at: NOW,
    last_turn_at: NOW,
    archived_at: null,
    ...overrides,
  }
}

function render(ui: React.ReactElement): ReturnType<typeof rtlRender> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

function props() {
  return {
    patientId: "patient-1",
    callerFeatureKey: "session_prep",
    activeConversationId: null,
    onSelectConversation: vi.fn(),
    onNewConversation: vi.fn(),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue({
    data: [conversation({ id: "conv-1", title: "First chat" })],
    total: 1,
  })
  mockUpdate.mockResolvedValue(conversation({ id: "conv-1" }))
  mockDelete.mockResolvedValue(undefined)
})

describe("ChatHistorySidebar — rename", () => {
  it("submits the new title on Enter", async () => {
    render(<ChatHistorySidebar {...props()} />)

    fireEvent.click(await screen.findByRole("button", { name: "Rename" }))
    const input = screen.getByRole("textbox")
    fireEvent.change(input, { target: { value: "Renamed chat" } })
    fireEvent.submit(input.closest("form")!)

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith("conv-1", { title: "Renamed chat" }),
    )
  })

  it("cancels on Escape without calling the API", async () => {
    render(<ChatHistorySidebar {...props()} />)

    fireEvent.click(await screen.findByRole("button", { name: "Rename" }))
    const input = screen.getByRole("textbox")
    fireEvent.change(input, { target: { value: "Abandoned edit" } })
    fireEvent.keyDown(input, { key: "Escape" })

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Rename" })).toBeInTheDocument(),
    )
    expect(mockUpdate).not.toHaveBeenCalled()
  })
})

describe("ChatHistorySidebar — archive", () => {
  it("archives the clicked row and refetches the list", async () => {
    render(<ChatHistorySidebar {...props()} />)

    fireEvent.click(await screen.findByRole("button", { name: "Archive" }))

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith("conv-1", { archive: true }),
    )
    // Initial load + the refetch triggered after the archive resolves.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
  })
})

describe("ChatHistorySidebar — delete", () => {
  it("shows a confirm panel and only deletes the opened row on confirm", async () => {
    render(<ChatHistorySidebar {...props()} />)

    expect(screen.queryByTestId("chat-history-delete-confirm")).not.toBeInTheDocument()

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }))
    const confirmPanel = screen.getByTestId("chat-history-delete-confirm")

    fireEvent.click(within(confirmPanel).getByRole("button", { name: "Delete" }))

    await waitFor(() =>
      expect(mockDelete).toHaveBeenCalledWith("conv-1", "purge"),
    )
  })

  it("does not delete anything on cancel", async () => {
    render(<ChatHistorySidebar {...props()} />)

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }))
    const confirmPanel = screen.getByTestId("chat-history-delete-confirm")

    fireEvent.click(within(confirmPanel).getByRole("button", { name: "Cancel" }))

    await waitFor(() =>
      expect(screen.queryByTestId("chat-history-delete-confirm")).not.toBeInTheDocument(),
    )
    expect(mockDelete).not.toHaveBeenCalled()
  })
})

describe("ChatHistorySidebar — show archived", () => {
  it("re-lists with includeArchived when the checkbox is toggled", async () => {
    render(<ChatHistorySidebar {...props()} />)

    await screen.findByText("First chat")
    fireEvent.click(screen.getByRole("checkbox", { name: "Show archived" }))

    await waitFor(() =>
      expect(mockList).toHaveBeenLastCalledWith({
        patientId: "patient-1",
        callerFeatureKey: "session_prep",
        includeArchived: true,
      }),
    )
  })
})
