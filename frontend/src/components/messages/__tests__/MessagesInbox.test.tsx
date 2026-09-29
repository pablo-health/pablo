// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MessagesInbox tests — the practice's two views of what clients wrote.
 *
 * Conversations are grouped, unread first, and show no message text.
 * Messages are ungrouped: every client message on its own row. Either opens
 * the conversation, which is marked read once it loads and answered from a
 * reply box. With the portal off there is nothing to read, and the page says
 * where to turn it on.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { MessagesInbox } from "../MessagesInbox"
import { renderWithProviders } from "@/test/renderWithProviders"

const api = {
  listInboxThreads: vi.fn(),
  listInboxMessages: vi.fn(),
  getThread: vi.fn(),
  replyToThread: vi.fn(),
  markThreadRead: vi.fn(),
  closeThread: vi.fn(),
  reopenThread: vi.fn(),
}
const mockPortal = vi.fn()

vi.mock("@/lib/api/messageInbox", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/messageInbox")>()),
  listInboxThreads: (...a: unknown[]) => api.listInboxThreads(...a),
  listInboxMessages: (...a: unknown[]) => api.listInboxMessages(...a),
  getThread: (...a: unknown[]) => api.getThread(...a),
  replyToThread: (...a: unknown[]) => api.replyToThread(...a),
  markThreadRead: (...a: unknown[]) => api.markThreadRead(...a),
  closeThread: (...a: unknown[]) => api.closeThread(...a),
  reopenThread: (...a: unknown[]) => api.reopenThread(...a),
}))

vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockPortal(...a),
}))

const THREADS = [
  {
    id: "t-ada",
    subject: "Refill question",
    status: "open",
    created_at: "2026-09-28T09:00:00Z",
    last_message_at: "2026-09-28T10:00:00Z",
    closed_at: null,
    assigned_user_id: null,
    unread_count: 2,
    patient_id: "p-ada",
    patient_name: "Ada Lovelace",
  },
  {
    id: "t-grace",
    subject: null,
    status: "open",
    created_at: "2026-09-28T08:00:00Z",
    last_message_at: "2026-09-28T11:00:00Z",
    closed_at: null,
    assigned_user_id: null,
    unread_count: 0,
    patient_id: "p-grace",
    patient_name: "Grace Hopper",
  },
]

function message(id: string, body: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    thread_id: "t-ada",
    sender: "patient",
    body,
    created_at: "2026-09-28T10:00:00Z",
    read_at: null,
    attachments: [],
    patient_id: "p-ada",
    patient_name: "Ada Lovelace",
    thread_subject: "Refill question",
    thread_status: "open",
    unread: true,
    ...extra,
  }
}

describe("MessagesInbox", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPortal.mockResolvedValue({ enabled: true, decided: true })
    api.listInboxThreads.mockResolvedValue({ data: THREADS, total: 2, has_more: false })
    api.listInboxMessages.mockResolvedValue({
      data: [message("m2", "Can I get more?"), message("m1", "Hello there", { unread: false })],
      total: 2,
      has_more: false,
    })
    api.getThread.mockResolvedValue({
      id: "t-ada",
      subject: "Refill question",
      status: "open",
      created_at: "2026-09-28T09:00:00Z",
      last_message_at: "2026-09-28T10:00:00Z",
      closed_at: null,
      messages: [message("m1", "Hello there"), message("m2", "Can I get more?")],
    })
    api.markThreadRead.mockResolvedValue({})
    api.replyToThread.mockResolvedValue(message("m3", "Yes", { sender: "clinician" }))
    api.closeThread.mockResolvedValue({})
  })

  it("lists conversations with whose they are and how many are unread, and no message text", async () => {
    renderWithProviders(<MessagesInbox />)

    const rows = await screen.findAllByTestId("conversation-row")
    expect(rows[0]).toHaveTextContent("Ada Lovelace")
    expect(rows[0]).toHaveTextContent("Refill question")
    expect(within(rows[0]).getByLabelText("2 unread")).toBeInTheDocument()
    expect(rows[1]).toHaveTextContent("No subject")
    expect(screen.queryByText("Can I get more?")).not.toBeInTheDocument()
    expect(api.listInboxThreads).toHaveBeenCalledWith("open")
  })

  it("shows closed conversations on their own", async () => {
    renderWithProviders(<MessagesInbox />)
    await screen.findAllByTestId("conversation-row")

    await userEvent.click(screen.getByRole("button", { name: "Closed" }))

    await waitFor(() => expect(api.listInboxThreads).toHaveBeenCalledWith("closed"))
  })

  it("lists every client message on its own row in the Messages view", async () => {
    renderWithProviders(<MessagesInbox />)

    await userEvent.click(await screen.findByRole("tab", { name: "Messages" }))

    const rows = await screen.findAllByTestId("message-row")
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Can I get more?"),
      expect.stringContaining("Hello there"),
    ])
    expect(rows[0]).toHaveAttribute("data-unread", "true")
    expect(rows[1]).toHaveAttribute("data-unread", "false")

    await userEvent.click(screen.getByLabelText("Unread only"))
    await waitFor(() => expect(api.listInboxMessages).toHaveBeenCalledWith(true))
  })

  it("opens a message's conversation and marks it read once it has loaded", async () => {
    renderWithProviders(<MessagesInbox />)
    await userEvent.click(await screen.findByRole("tab", { name: "Messages" }))

    await userEvent.click((await screen.findAllByTestId("message-row"))[0])

    const thread = await screen.findByTestId("thread-view")
    expect(within(thread).getAllByTestId("thread-message-client")).toHaveLength(2)
    expect(within(thread).getByRole("link", { name: "Open chart" })).toHaveAttribute(
      "href",
      "/dashboard/patients/p-ada",
    )
    await waitFor(() => expect(api.markThreadRead).toHaveBeenCalledWith("t-ada"))
    expect(api.markThreadRead).toHaveBeenCalledTimes(1)
  })

  it("sends a reply into the conversation and clears the box", async () => {
    renderWithProviders(<MessagesInbox />)
    await userEvent.click((await screen.findAllByTestId("conversation-row"))[0])

    const box = await screen.findByTestId("thread-reply-input")
    expect(screen.getByTestId("thread-reply-send")).toBeDisabled()
    await userEvent.type(box, "  Yes, sent to your pharmacy.  ")
    await userEvent.click(screen.getByTestId("thread-reply-send"))

    expect(api.replyToThread).toHaveBeenCalledWith("t-ada", "Yes, sent to your pharmacy.")
    await waitFor(() => expect(box).toHaveValue(""))
  })

  it("closes a conversation", async () => {
    renderWithProviders(<MessagesInbox />)
    await userEvent.click((await screen.findAllByTestId("conversation-row"))[0])

    await userEvent.click(await screen.findByRole("button", { name: "Close" }))

    expect(api.closeThread).toHaveBeenCalledWith("t-ada")
  })

  it("says where to turn the portal on when it is off", async () => {
    mockPortal.mockResolvedValue({ enabled: false, decided: true })
    renderWithProviders(<MessagesInbox />)

    expect(await screen.findByTestId("messages-portal-off")).toHaveTextContent(
      "Your client portal is off.",
    )
    expect(
      within(screen.getByTestId("messages-portal-off")).getByRole("link", {
        name: "Client portal settings",
      }),
    ).toHaveAttribute("href", "/dashboard/settings/portal")
    await userEvent.click((await screen.findAllByTestId("conversation-row"))[0])
    expect(await screen.findByTestId("thread-replies-off")).toHaveTextContent(
      "Turn the client portal back on to reply.",
    )
  })

  it("keeps what clients already sent readable when Messages is off, and offers no reply", async () => {
    mockPortal.mockResolvedValue({
      enabled: true,
      decided: true,
      modules: { intake: true, messaging: false },
    })
    renderWithProviders(<MessagesInbox />)

    expect(await screen.findByTestId("messages-portal-off")).toHaveTextContent(
      "Messages are turned off in your client portal.",
    )
    // The conversations are still the practice's to read.
    await userEvent.click((await screen.findAllByTestId("conversation-row"))[0])
    const thread = await screen.findByTestId("thread-view")
    expect(within(thread).getAllByTestId("thread-message-client")).toHaveLength(2)
    // A reply would land where the client cannot open it: no reply box.
    expect(within(thread).queryByTestId("thread-reply-input")).not.toBeInTheDocument()
    expect(within(thread).getByTestId("thread-replies-off")).toHaveTextContent(
      "Turn Messages back on to reply.",
    )
  })

  it("marks an open conversation read again when a new message arrives in it", async () => {
    renderWithProviders(<MessagesInbox />)
    await userEvent.click((await screen.findAllByTestId("conversation-row"))[0])
    await waitFor(() => expect(api.markThreadRead).toHaveBeenCalledTimes(1))

    api.getThread.mockResolvedValue({
      id: "t-ada",
      subject: "Refill question",
      status: "open",
      created_at: "2026-09-28T09:00:00Z",
      last_message_at: "2026-09-28T12:00:00Z",
      closed_at: null,
      messages: [
        message("m1", "Hello there"),
        message("m2", "Can I get more?"),
        message("m3", "One more thing"),
      ],
    })
    // Stand in for the poll: the reply refetch re-reads the thread.
    await userEvent.type(await screen.findByTestId("thread-reply-input"), "ok")
    await userEvent.click(screen.getByTestId("thread-reply-send"))

    await waitFor(() => expect(api.markThreadRead).toHaveBeenCalledTimes(2))
  })

  it("works as usual where the deployment does not list Messages as a choice", async () => {
    mockPortal.mockResolvedValue({ enabled: true, decided: true, modules: { intake: true } })
    renderWithProviders(<MessagesInbox />)

    expect(await screen.findAllByTestId("conversation-row")).toHaveLength(2)
  })

  it("says so when there is nothing to read", async () => {
    api.listInboxThreads.mockResolvedValue({ data: [], total: 0, has_more: false })
    renderWithProviders(<MessagesInbox />)

    expect(await screen.findByTestId("messages-empty")).toHaveTextContent("Nothing here.")
  })
})
