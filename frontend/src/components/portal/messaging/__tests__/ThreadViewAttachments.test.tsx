// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ThreadView, the attachment half: what arrived is listed and openable.
 *
 * A link rather than a preview, because the URL behind it is minted per
 * click and short-lived — so the click is what asks for it.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ThreadView } from "../ThreadView"
import type { PatientMessageThreadDetail } from "@/lib/api/patientMessages"

const ATTACHMENT = {
  document_id: "doc-1",
  filename: "after-visit-summary.pdf",
  mime_type: "application/pdf",
  size_bytes: 1024 * 400,
}

function thread(
  attachments: (typeof ATTACHMENT)[] | undefined,
): PatientMessageThreadDetail {
  return {
    id: "t1",
    subject: "Refill",
    status: "open",
    created_at: "2026-09-20T09:00:00Z",
    last_message_at: "2026-09-20T09:05:00Z",
    messages: [
      {
        id: "m1",
        thread_id: "t1",
        sender: "clinician",
        body: "Here is the summary.",
        created_at: "2026-09-20T09:05:00Z",
        attachments,
      },
    ],
  }
}

function renderThread(
  attachments: (typeof ATTACHMENT)[] | undefined,
  onOpenAttachment?: (attachment: typeof ATTACHMENT) => void,
) {
  render(
    <ThreadView
      thread={thread(attachments)}
      onMarkRead={vi.fn()}
      onSend={vi.fn()}
      sending={false}
      onOpenAttachment={onOpenAttachment}
    />,
  )
}

describe("ThreadView attachments", () => {
  it("lists what a message carried, with its size", () => {
    renderThread([ATTACHMENT], vi.fn())

    const link = screen.getByTestId("portal-messaging-attachment-doc-1")
    expect(link.textContent).toBe("after-visit-summary.pdf")
    expect(screen.getByTestId("portal-messaging-attachments-m1").textContent).toContain(
      "400 KB",
    )
  })

  it("asks for the file only when the patient clicks it", async () => {
    const onOpenAttachment = vi.fn()
    const user = userEvent.setup()
    renderThread([ATTACHMENT], onOpenAttachment)

    expect(onOpenAttachment).not.toHaveBeenCalled()
    await user.click(screen.getByTestId("portal-messaging-attachment-doc-1"))

    expect(onOpenAttachment).toHaveBeenCalledWith(ATTACHMENT)
  })

  it("renders a message that carried nothing exactly as before", () => {
    renderThread(undefined, vi.fn())

    expect(screen.queryByTestId("portal-messaging-attachments-m1")).toBeNull()
    expect(screen.getByTestId("portal-messaging-thread-view").textContent).toContain(
      "Here is the summary.",
    )
  })
})
