// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { IntegrationSettings } from "../IntegrationSettings"
import type { ICalConnectionStatus } from "@/lib/api/scheduling"

const { getStatus } = vi.hoisted(() => ({ getStatus: vi.fn() }))

vi.mock("@/lib/api/scheduling", () => ({
  getICalSyncStatus: getStatus,
  configureICalSync: vi.fn(),
  disconnectICalSync: vi.fn(),
  importClients: vi.fn(),
}))

function connection(overrides: Partial<ICalConnectionStatus> = {}): ICalConnectionStatus {
  return {
    ehr_system: "simplepractice",
    connected: true,
    last_synced_at: null,
    last_sync_error: null,
    title_style: null,
    ...overrides,
  }
}

const NOTE =
  "This feed shows clients by their initials, so Pablo asks about every session. Showing full names in the calendar sync means fewer questions."

beforeEach(() => {
  getStatus.mockReset()
})

describe("IntegrationSettings", () => {
  it("says so when a feed shows clients by their initials", async () => {
    getStatus.mockResolvedValue({ connections: [connection({ title_style: "initials" })] })

    render(<IntegrationSettings />)

    expect(await screen.findByTestId("feed-initials-note")).toHaveTextContent(NOTE)
  })

  it("says nothing about a feed that shows full names", async () => {
    getStatus.mockResolvedValue({ connections: [connection({ title_style: "names" })] })

    render(<IntegrationSettings />)

    await screen.findByRole("button", { name: "Disconnect SimplePractice" })
    expect(screen.queryByTestId("feed-initials-note")).not.toBeInTheDocument()
  })

  it("says nothing before a feed has been read", async () => {
    getStatus.mockResolvedValue({ connections: [connection()] })

    render(<IntegrationSettings />)

    await screen.findByRole("button", { name: "Disconnect SimplePractice" })
    expect(screen.queryByTestId("feed-initials-note")).not.toBeInTheDocument()
  })
})
