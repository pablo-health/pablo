// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Record activity" in the chart header: shown to the practice owner only,
 * and reading what the server sent as plain sentences in the practice's word.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RecordActivity } from "../RecordActivity"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { RecordActivityItem, RecordActivityPage } from "@/lib/api/patientActivity"

const mockStatus = vi.fn()
const mockActivity = vi.fn()

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: (...a: unknown[]) => mockStatus(...a),
}))

vi.mock("@/lib/api/patientActivity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/patientActivity")>()),
  getRecordActivity: (...a: unknown[]) => mockActivity(...a),
}))

function row(overrides: Partial<RecordActivityItem>): RecordActivityItem {
  return {
    id: crypto.randomUUID(),
    timestamp: "2026-10-01T15:04:00Z",
    actor_type: "clinician",
    actor_name: null,
    is_you: false,
    action: "patient_viewed",
    resource_type: "patient",
    ...overrides,
  }
}

function page(data: RecordActivityItem[], next_cursor: string | null = null): RecordActivityPage {
  return { data, limit: 50, next_cursor }
}

describe("RecordActivity", () => {
  beforeEach(() => {
    mockStatus.mockReset()
    mockActivity.mockReset()
  })

  it("is not offered to anyone but the practice owner", async () => {
    mockStatus.mockResolvedValue({ can_view_record_activity: false, is_practice_owner: true })

    renderWithProviders(<RecordActivity patientId="p-1" />)

    // Wait for the status to arrive, then confirm nothing rendered and nothing was read.
    await vi.waitFor(() => expect(mockStatus).toHaveBeenCalled())
    expect(screen.queryByRole("button", { name: /record activity/i })).toBeNull()
    expect(mockActivity).not.toHaveBeenCalled()
  })

  it("reads the record only once the owner opens it, and says who did what", async () => {
    mockStatus.mockResolvedValue({ can_view_record_activity: true })
    mockActivity.mockResolvedValue(
      page([
        row({ actor_type: "patient", action: "patient_message_sent" }),
        row({ actor_type: "patient", action: "patient_portal_session_redeemed" }),
        row({ is_you: true, action: "patient_viewed" }),
        row({ actor_name: "Riley Colleague", action: "note_signed" }),
        row({ actor_type: "system", actor_name: "appointment_reminders", action: "reminder_sent" }),
      ])
    )

    renderWithProviders(<RecordActivity patientId="p-1" />)
    const link = await screen.findByRole("button", { name: /record activity/i })
    expect(mockActivity).not.toHaveBeenCalled()

    await userEvent.click(link)

    const list = await screen.findByRole("list", { name: "Record activity" })
    const lines = within(list)
      .getAllByRole("listitem")
      .map((item) => item.querySelector("p")?.textContent)
    expect(lines).toEqual([
      "Client sent a message",
      "Client signed in to the portal",
      "You viewed the chart",
      "Riley Colleague signed a note",
      // An action with no phrase still reads plainly, and a job's internal
      // name never reaches the screen.
      "Pablo: Reminder sent",
    ])
    expect(mockActivity).toHaveBeenCalledWith("p-1", { cursor: null, limit: 50 })
  })

  it("pages back when there is more", async () => {
    mockStatus.mockResolvedValue({ can_view_record_activity: true })
    mockActivity
      .mockResolvedValueOnce(page([row({ action: "patient_viewed" })], "older"))
      .mockResolvedValueOnce(page([row({ action: "patient_updated" })]))

    renderWithProviders(<RecordActivity patientId="p-1" />)
    await userEvent.click(await screen.findByRole("button", { name: /record activity/i }))
    await userEvent.click(await screen.findByRole("button", { name: "Load older" }))

    expect(await screen.findByText("A clinician edited the chart")).toBeTruthy()
    expect(mockActivity).toHaveBeenLastCalledWith("p-1", { cursor: "older", limit: 50 })
    expect(screen.queryByRole("button", { name: "Load older" })).toBeNull()
  })
})
