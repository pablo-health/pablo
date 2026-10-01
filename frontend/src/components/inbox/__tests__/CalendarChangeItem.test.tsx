// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A calendar change settled from the Inbox, with the same choices the
 * calendar offers for each state the sync leaves behind.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { InboxItem } from "@/lib/api/inbox"
import { CalendarChangeItem } from "../CalendarChangeItem"

const scheduling = { resolveGoogleChange: vi.fn(), resolveHeldGoogleRemovals: vi.fn() }
vi.mock("@/lib/api/scheduling", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/scheduling")>()),
  resolveGoogleChange: (...a: unknown[]) => scheduling.resolveGoogleChange(...a),
  resolveHeldGoogleRemovals: (...a: unknown[]) => scheduling.resolveHeldGoogleRemovals(...a),
}))

function change(status: string): InboxItem {
  return {
    kind: "calendar_change",
    source_id: "a1",
    patient_id: "p1",
    patient_name: "Ada Lovelace",
    title: "Google Calendar has a different time for this session",
    detail: null,
    occurred_at: "2026-09-28T10:00:00Z",
    severity: "normal",
    href: "/dashboard/calendar",
    context: { appointment_id: "a1", google_sync_status: status, start_at: "2026-10-02T14:00:00Z" },
    disposition: null,
    resolved_at: null,
    snoozed_until: null,
  }
}

describe("CalendarChangeItem", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    scheduling.resolveGoogleChange.mockResolvedValue({})
    scheduling.resolveHeldGoogleRemovals.mockResolvedValue({ count: 3 })
  })

  it("keeps Pablo's time or takes Google's for a move it could not follow", async () => {
    renderWithProviders(<CalendarChangeItem item={change("external_change")} onClose={() => undefined} />)

    await userEvent.click(screen.getByRole("button", { name: "Use Google's time" }))
    expect(scheduling.resolveGoogleChange).toHaveBeenCalledWith("a1", "accept_google")
    await userEvent.click(screen.getByRole("button", { name: "Keep this time" }))
    expect(scheduling.resolveGoogleChange).toHaveBeenCalledWith("a1", "keep_pablo")
  })

  it("undoes a quiet cancellation", async () => {
    renderWithProviders(<CalendarChangeItem item={change("removed_in_google")} onClose={() => undefined} />)

    await userEvent.click(screen.getByRole("button", { name: "Undo" }))
    expect(scheduling.resolveGoogleChange).toHaveBeenCalledWith("a1", "keep_pablo")
  })

  it("settles a held bulk removal all at once", async () => {
    renderWithProviders(<CalendarChangeItem item={change("missing_in_google")} onClose={() => undefined} />)

    await userEvent.click(screen.getByRole("button", { name: "Put back in Google Calendar" }))
    expect(scheduling.resolveHeldGoogleRemovals).toHaveBeenCalledWith("keep_pablo")
    expect(scheduling.resolveGoogleChange).not.toHaveBeenCalled()
  })
})
