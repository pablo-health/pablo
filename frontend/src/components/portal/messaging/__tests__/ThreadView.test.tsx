// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ThreadView: two voices not three, mark-read fired once per thread,
 * and no message body reaching the console.
 */

import { describe, expect, it, vi, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"
import type { PatientMessageThreadDetail } from "@/lib/api/patientMessages"
import { ThreadView, type ThreadViewProps } from "../ThreadView"

const thread: PatientMessageThreadDetail = {
  id: "t1",
  subject: "Refill question",
  status: "open",
  created_at: "2026-09-01T15:00:00Z",
  last_message_at: "2026-09-03T15:00:00Z",
  messages: [
    {
      id: "m1",
      thread_id: "t1",
      sender: "patient",
      body: "A thing I asked",
      created_at: "2026-09-01T15:00:00Z",
    },
    {
      id: "m2",
      thread_id: "t1",
      sender: "clinician",
      body: "A thing answered",
      created_at: "2026-09-02T15:00:00Z",
    },
    {
      id: "m3",
      thread_id: "t1",
      sender: "practice",
      body: "A thing acknowledged",
      created_at: "2026-09-03T15:00:00Z",
    },
  ],
}

function renderThread(overrides: Partial<ThreadViewProps> = {}) {
  const props: ThreadViewProps = {
    thread,
    onMarkRead: vi.fn(),
    onSend: vi.fn().mockResolvedValue(undefined),
    sending: false,
    ...overrides,
  }
  return { props, ...render(<ThreadView {...props} />) }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe("ThreadView", () => {
  it("distinguishes the patient's messages from the practice's", () => {
    renderThread()

    expect(
      screen.getByTestId("portal-messaging-message-m1").getAttribute("data-sender"),
    ).toBe("patient")
    expect(
      screen.getByTestId("portal-messaging-message-m2").getAttribute("data-sender"),
    ).toBe("practice")
  })

  it("renders an automatic practice message as the practice, not a third voice", () => {
    renderThread()

    const automatic = screen.getByTestId("portal-messaging-message-m3")
    expect(automatic.getAttribute("data-sender")).toBe("practice")
    expect(automatic.textContent).toContain("Your practice")
  })

  it("shows the thread status without offering to close it", () => {
    renderThread()

    expect(screen.getByTestId("portal-messaging-thread-status").textContent).toBe("open")
    expect(screen.queryByRole("button", { name: /close/i })).toBeNull()
  })

  it("marks the thread read once, not once per render", () => {
    const onMarkRead = vi.fn()
    const { rerender } = render(
      <ThreadView
        thread={thread}
        onMarkRead={onMarkRead}
        onSend={vi.fn()}
        sending={false}
      />,
    )
    rerender(
      <ThreadView
        thread={{ ...thread }}
        onMarkRead={onMarkRead}
        onSend={vi.fn()}
        sending={false}
      />,
    )

    expect(onMarkRead).toHaveBeenCalledTimes(1)
    expect(onMarkRead).toHaveBeenCalledWith("t1")
  })

  it("marks a different thread read when one is opened", () => {
    const onMarkRead = vi.fn()
    const { rerender } = render(
      <ThreadView
        thread={thread}
        onMarkRead={onMarkRead}
        onSend={vi.fn()}
        sending={false}
      />,
    )
    rerender(
      <ThreadView
        thread={{ ...thread, id: "t2", messages: [] }}
        onMarkRead={onMarkRead}
        onSend={vi.fn()}
        sending={false}
      />,
    )

    expect(onMarkRead.mock.calls.map((call) => call[0])).toEqual(["t1", "t2"])
  })

  it("puts no message body on the console", () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) =>
      vi.spyOn(console, level).mockImplementation(() => {}),
    )

    renderThread()

    for (const spy of spies) {
      for (const call of spy.mock.calls) {
        expect(JSON.stringify(call)).not.toContain("A thing I asked")
        expect(JSON.stringify(call)).not.toContain("A thing answered")
      }
    }
  })
})
