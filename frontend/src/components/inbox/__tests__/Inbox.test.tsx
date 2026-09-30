// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Inbox: one list of what is waiting, narrowed by kind, split into Open
 * and Done, with each item opened through its kind's renderer.
 *
 * A client message opens inside its conversation, and replying answers that
 * message only. Whether the client's earlier unanswered messages go with it
 * is asked once (Yes / Always / Don't ask again), and a remembered "always"
 * says what it did and offers Undo. With the portal off, what clients sent
 * stays readable and there is no reply box.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { Inbox } from "../Inbox"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { InboxItem } from "@/lib/api/inbox"

const nav = vi.hoisted(() => {
  let search = ""
  const listeners = new Set<() => void>()
  return {
    get: () => search,
    set(next: string) {
      search = next
      listeners.forEach((listener) => listener())
    },
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    replace: vi.fn(),
  }
})

vi.mock("next/navigation", async () => {
  const React = await import("react")
  return {
    usePathname: () => "/dashboard/inbox",
    useRouter: () => ({
      replace: (url: string) => {
        nav.replace(url)
        nav.set(url.split("?")[1] ?? "")
      },
    }),
    useSearchParams: () => new URLSearchParams(React.useSyncExternalStore(nav.subscribe, nav.get)),
  }
})

const inboxApi = {
  listInbox: vi.fn(),
  dismissInboxItem: vi.fn(),
  snoozeInboxItem: vi.fn(),
  restoreInboxItem: vi.fn(),
  handleEarlierMessages: vi.fn(),
}
vi.mock("@/lib/api/inbox", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/inbox")>()),
  listInbox: (...a: unknown[]) => inboxApi.listInbox(...a),
  dismissInboxItem: (...a: unknown[]) => inboxApi.dismissInboxItem(...a),
  snoozeInboxItem: (...a: unknown[]) => inboxApi.snoozeInboxItem(...a),
  restoreInboxItem: (...a: unknown[]) => inboxApi.restoreInboxItem(...a),
  handleEarlierMessages: (...a: unknown[]) => inboxApi.handleEarlierMessages(...a),
}))

const threadApi = {
  getThread: vi.fn(),
  replyToThread: vi.fn(),
  markThreadRead: vi.fn(),
}
vi.mock("@/lib/api/messageInbox", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/messageInbox")>()),
  getThread: (...a: unknown[]) => threadApi.getThread(...a),
  replyToThread: (...a: unknown[]) => threadApi.replyToThread(...a),
  markThreadRead: (...a: unknown[]) => threadApi.markThreadRead(...a),
}))

const mockPortal = vi.fn()
vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockPortal(...a),
}))

const prefsApi = { getPreferences: vi.fn(), savePreferences: vi.fn() }
vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getPreferences: (...a: unknown[]) => prefsApi.getPreferences(...a),
  savePreferences: (...a: unknown[]) => prefsApi.savePreferences(...a),
}))

function item(overrides: Partial<InboxItem>): InboxItem {
  return {
    kind: "refill",
    source_id: "r1",
    patient_id: "p-ada",
    patient_name: "Ada Lovelace",
    title: "Refill request: Sertraline 50 mg",
    detail: "Out on Friday",
    occurred_at: "2026-09-28T10:00:00Z",
    severity: "normal",
    href: "/dashboard/refills",
    context: {},
    disposition: null,
    resolved_at: null,
    snoozed_until: null,
    ...overrides,
  }
}

const MESSAGE = item({
  kind: "portal_message",
  source_id: "m2",
  title: "Refill question",
  detail: "Can I get more?",
  href: "/dashboard/inbox?filter=messages&item=m2",
  context: { thread_id: "t-ada" },
})
const REFILL = item({})

function threadMessage(id: string, body: string, sender = "patient") {
  return {
    id,
    thread_id: "t-ada",
    sender,
    body,
    created_at: "2026-09-28T10:00:00Z",
    read_at: null,
    attachments: [],
  }
}

const PREFERENCES = {
  default_video_platform: "zoom",
  default_session_type: "individual",
  default_duration_minutes: 50,
  auto_transcribe: true,
  quality_preset: "balanced",
  therapist_display_name: null,
  calendar_default_view: "week",
  timezone: "America/New_York",
  theme: "warm-paper",
  calendar_density: "balanced",
  inbox_reply_earlier_messages: "ask",
}

function reply(inbox: { resolved_ids: string[]; earlier_open_ids: string[]; earlier_handled_ids: string[] }) {
  return { ...threadMessage("m9", "Yes", "clinician"), inbox }
}

async function openMessage() {
  await userEvent.click(
    (await screen.findAllByTestId("inbox-row")).find((row) => row.dataset.kind === "portal_message")!,
  )
  return screen.findByTestId("thread-view")
}

async function sendReply(text = "Yes, sent.") {
  await userEvent.type(await screen.findByTestId("thread-reply-input"), text)
  await userEvent.click(screen.getByTestId("thread-reply-send"))
}

describe("Inbox", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    nav.set("")
    mockPortal.mockResolvedValue({ enabled: true, decided: true })
    prefsApi.getPreferences.mockResolvedValue(PREFERENCES)
    prefsApi.savePreferences.mockImplementation((prefs: unknown) => Promise.resolve(prefs))
    inboxApi.listInbox.mockResolvedValue({ data: [MESSAGE, REFILL], total: 2 })
    inboxApi.dismissInboxItem.mockResolvedValue({ ...REFILL, disposition: "dismissed" })
    inboxApi.snoozeInboxItem.mockResolvedValue({ ...REFILL, disposition: "snoozed" })
    inboxApi.restoreInboxItem.mockResolvedValue(REFILL)
    inboxApi.handleEarlierMessages.mockResolvedValue({ handled_ids: ["m0", "m1"] })
    threadApi.getThread.mockResolvedValue({
      id: "t-ada",
      subject: "Refill question",
      status: "open",
      created_at: "2026-09-28T09:00:00Z",
      last_message_at: "2026-09-28T10:00:00Z",
      closed_at: null,
      messages: [threadMessage("m1", "Hello there"), threadMessage("m2", "Can I get more?")],
    })
    threadApi.markThreadRead.mockResolvedValue({})
    threadApi.replyToThread.mockResolvedValue(
      reply({ resolved_ids: ["m2"], earlier_open_ids: [], earlier_handled_ids: [] }),
    )
  })

  it("lists every open item, whose it is and what it is", async () => {
    renderWithProviders(<Inbox />)

    const rows = await screen.findAllByTestId("inbox-row")
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent("Ada Lovelace")
    expect(rows[0]).toHaveTextContent("Can I get more?")
    expect(rows[1]).toHaveTextContent("Refill request: Sertraline 50 mg")
    expect(inboxApi.listInbox).toHaveBeenCalledWith("open", [])
  })

  it("narrows to one kind with a filter and remembers it in the address", async () => {
    renderWithProviders(<Inbox />)
    await screen.findAllByTestId("inbox-row")

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "All",
      "Messages",
      "Refills",
      "Intake",
      "Notes",
      "Calendar",
    ])
    await userEvent.click(screen.getByRole("tab", { name: "Refills" }))

    await waitFor(() => expect(inboxApi.listInbox).toHaveBeenCalledWith("open", ["refill"]))
    expect(nav.replace).toHaveBeenLastCalledWith("/dashboard/inbox?filter=refills")
  })

  it("switches to what was handled, and puts an item back from there", async () => {
    inboxApi.listInbox.mockImplementation((view: string) =>
      Promise.resolve(
        view === "done"
          ? { data: [{ ...REFILL, disposition: "dismissed", resolved_at: "2026-09-28T11:00:00Z" }], total: 1 }
          : { data: [MESSAGE, REFILL], total: 2 },
      ),
    )
    renderWithProviders(<Inbox />)
    await screen.findAllByTestId("inbox-row")

    await userEvent.click(screen.getByRole("button", { name: "Done" }))

    await waitFor(() => expect(inboxApi.listInbox).toHaveBeenCalledWith("done", []))
    const row = await screen.findByTestId("inbox-row-disposition")
    expect(row).toHaveTextContent("Dismissed")
    await userEvent.click(screen.getByTestId("inbox-row"))
    await userEvent.click(screen.getByRole("button", { name: "Restore" }))

    expect(inboxApi.restoreInboxItem).toHaveBeenCalledWith("refill", "r1")
  })

  it("dismisses and snoozes an open item", async () => {
    renderWithProviders(<Inbox />)
    const refill = (await screen.findAllByTestId("inbox-row"))[1]

    await userEvent.click(refill)
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }))
    expect(inboxApi.dismissInboxItem).toHaveBeenCalledWith("refill", "r1")

    await userEvent.click(refill)
    await userEvent.click(screen.getByRole("button", { name: "Snooze until tomorrow" }))
    expect(inboxApi.snoozeInboxItem).toHaveBeenCalledWith("refill", "r1", expect.any(Date))
    const until = inboxApi.snoozeInboxItem.mock.calls[0][2] as Date
    expect(until.getTime()).toBeGreaterThan(Date.now())
  })

  it("links a refill to where it is answered", async () => {
    renderWithProviders(<Inbox />)
    await userEvent.click((await screen.findAllByTestId("inbox-row"))[1])

    expect(screen.getByRole("link", { name: "Answer on the Refills page" })).toHaveAttribute(
      "href",
      "/dashboard/refills",
    )
    expect(screen.getByTestId("inbox-item-refill")).toHaveTextContent("Note from client: Out on Friday")
  })

  it("opens a client message inside its conversation, marked, and answers that message", async () => {
    renderWithProviders(<Inbox />)

    const thread = await openMessage()
    const messages = await within(thread).findAllByTestId("thread-message-client")
    expect(messages).toHaveLength(2)
    expect(messages[1]).toHaveAttribute("data-highlighted", "true")
    expect(messages[0]).not.toHaveAttribute("data-highlighted")

    await sendReply()

    expect(threadApi.replyToThread).toHaveBeenCalledWith("t-ada", "Yes, sent.", "m2")
    // Nothing earlier is open, so nothing is asked.
    expect(screen.queryByTestId("inbox-earlier-prompt")).not.toBeInTheDocument()
  })

  it("opens the message a link names", async () => {
    nav.set("filter=messages&item=m2")
    renderWithProviders(<Inbox />)

    expect(await screen.findByTestId("thread-view")).toBeInTheDocument()
    expect(inboxApi.listInbox).toHaveBeenCalledWith("open", ["portal_message"])
  })

  describe("the client's earlier messages, after a reply", () => {
    beforeEach(() => {
      threadApi.replyToThread.mockResolvedValue(
        reply({ resolved_ids: ["m2"], earlier_open_ids: ["m0", "m1"], earlier_handled_ids: [] }),
      )
    })

    it("asks once, and Yes marks them handled this time only", async () => {
      renderWithProviders(<Inbox />)
      await openMessage()
      await sendReply()

      const prompt = await screen.findByTestId("inbox-earlier-prompt")
      expect(prompt).toHaveTextContent("Also mark Ada Lovelace's 2 earlier messages handled?")
      await userEvent.click(within(prompt).getByRole("button", { name: "Yes" }))

      expect(inboxApi.handleEarlierMessages).toHaveBeenCalledWith("m2")
      expect(prefsApi.savePreferences).not.toHaveBeenCalled()
      expect(await screen.findByTestId("inbox-earlier-handled")).toHaveTextContent(
        "2 earlier messages marked handled",
      )
    })

    it("says 'earlier message' for one", async () => {
      threadApi.replyToThread.mockResolvedValue(
        reply({ resolved_ids: ["m2"], earlier_open_ids: ["m1"], earlier_handled_ids: [] }),
      )
      renderWithProviders(<Inbox />)
      await openMessage()
      await sendReply()

      expect(await screen.findByTestId("inbox-earlier-prompt")).toHaveTextContent(
        "Also mark Ada Lovelace's earlier message handled?",
      )
    })

    it("Always marks them handled and remembers it", async () => {
      renderWithProviders(<Inbox />)
      await openMessage()
      await sendReply()

      await userEvent.click(
        within(await screen.findByTestId("inbox-earlier-prompt")).getByRole("button", { name: "Always" }),
      )

      expect(inboxApi.handleEarlierMessages).toHaveBeenCalledWith("m2")
      await waitFor(() =>
        expect(prefsApi.savePreferences).toHaveBeenCalledWith(
          expect.objectContaining({ inbox_reply_earlier_messages: "always" }),
          undefined,
        ),
      )
    })

    it("Don't ask again leaves them open and remembers it", async () => {
      renderWithProviders(<Inbox />)
      await openMessage()
      await sendReply()

      await userEvent.click(
        within(await screen.findByTestId("inbox-earlier-prompt")).getByRole("button", {
          name: "Don't ask again",
        }),
      )

      expect(inboxApi.handleEarlierMessages).not.toHaveBeenCalled()
      await waitFor(() =>
        expect(prefsApi.savePreferences).toHaveBeenCalledWith(
          expect.objectContaining({ inbox_reply_earlier_messages: "never" }),
          undefined,
        ),
      )
      expect(screen.queryByTestId("inbox-earlier-prompt")).not.toBeInTheDocument()
    })
  })

  it("under 'always', says what was marked handled and can undo it", async () => {
    threadApi.replyToThread.mockResolvedValue(
      reply({ resolved_ids: ["m2"], earlier_open_ids: [], earlier_handled_ids: ["m1"] }),
    )
    renderWithProviders(<Inbox />)
    await openMessage()
    await sendReply()

    const line = await screen.findByTestId("inbox-earlier-handled")
    expect(line).toHaveTextContent("1 earlier message marked handled")
    expect(screen.queryByTestId("inbox-earlier-prompt")).not.toBeInTheDocument()
    await userEvent.click(within(line).getByRole("button", { name: "Undo" }))

    expect(inboxApi.restoreInboxItem).toHaveBeenCalledWith("portal_message", "m1")
    await waitFor(() => expect(screen.queryByTestId("inbox-earlier-handled")).not.toBeInTheDocument())
  })

  it("keeps what clients sent readable with Messages off, and offers no reply", async () => {
    mockPortal.mockResolvedValue({ enabled: true, decided: true, modules: { messaging: false } })
    nav.set("filter=messages")
    renderWithProviders(<Inbox />)

    expect(await screen.findByTestId("messages-portal-off")).toHaveTextContent(
      "Messages are turned off in your client portal.",
    )
    const thread = await openMessage()
    expect(await within(thread).findAllByTestId("thread-message-client")).toHaveLength(2)
    expect(within(thread).queryByTestId("thread-reply-input")).not.toBeInTheDocument()
    expect(within(thread).getByTestId("thread-replies-off")).toHaveTextContent(
      "Turn Messages back on to reply.",
    )
  })

  it("says so when nothing needs the clinician", async () => {
    inboxApi.listInbox.mockResolvedValue({ data: [], total: 0 })
    renderWithProviders(<Inbox />)

    expect(await screen.findByTestId("inbox-empty")).toHaveTextContent("Nothing needs you right now.")
  })
})
