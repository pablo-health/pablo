// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PatientChatDialog tests (PABLO-6x5.6)
 *
 * The chat engine itself is covered by ChatPanel's suite; here we only
 * assert the modal wiring: the header button is closed by default and
 * opens a dialog that mounts ChatPanelWithHistory. Chat API + SSE are
 * mocked so nothing networks.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { PatientChatDialog } from "../PatientChatDialog"

vi.mock("@/lib/chat/api", () => ({
  listConversations: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  updateConversation: vi.fn(),
  deleteConversation: vi.fn(),
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

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "ChatDialogWrapper"
  return Wrapper
}

describe("PatientChatDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders a Chat button with the modal closed by default", () => {
    render(<PatientChatDialog patientId="p1" />, { wrapper: createWrapper() })

    expect(screen.getByRole("button", { name: /chat/i })).toBeInTheDocument()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("opens the modal and mounts the chat panel on click", async () => {
    const user = userEvent.setup()
    render(<PatientChatDialog patientId="p1" />, { wrapper: createWrapper() })

    await user.click(screen.getByRole("button", { name: /chat/i }))

    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent(/ask about this patient/i)
    await waitFor(() => {
      expect(
        dialog.querySelector('[data-slot="chat-panel-with-history"]'),
      ).toBeInTheDocument()
    })
  })
})
