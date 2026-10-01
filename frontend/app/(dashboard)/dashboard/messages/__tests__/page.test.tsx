// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Messages lives in the Inbox. The old address goes to its Messages filter,
 * and a link to one conversation goes to that client's latest message.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { waitFor } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import MessagesPage from "../page"

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace }),
  useSearchParams: () => new URLSearchParams(nav.search),
}))

const getThread = vi.fn()
vi.mock("@/lib/api/messageInbox", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/messageInbox")>()),
  getThread: (...a: unknown[]) => getThread(...a),
}))

function message(id: string, sender: string) {
  return { id, thread_id: "t1", sender, body: "x", created_at: "2026-09-28T10:00:00Z", read_at: null, attachments: [] }
}

describe("/dashboard/messages", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    nav.search = ""
  })

  it("goes to the Inbox's Messages filter", async () => {
    renderWithProviders(<MessagesPage />)

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/dashboard/inbox?filter=messages"))
    expect(getThread).not.toHaveBeenCalled()
  })

  it("takes a conversation link to that client's latest message", async () => {
    nav.search = "thread=t1"
    getThread.mockResolvedValue({
      id: "t1",
      subject: null,
      status: "open",
      created_at: "2026-09-28T09:00:00Z",
      last_message_at: "2026-09-28T12:00:00Z",
      closed_at: null,
      messages: [message("m1", "patient"), message("m2", "patient"), message("m3", "clinician")],
    })
    renderWithProviders(<MessagesPage />)

    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith("/dashboard/inbox?filter=messages&item=m2"),
    )
    expect(getThread).toHaveBeenCalledWith("t1")
  })
})
