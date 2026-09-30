// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import { NavBadge } from "../NavBadge"

const getInboxCount = vi.fn()
vi.mock("@/lib/api/inbox", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/inbox")>()),
  getInboxCount: (...a: unknown[]) => getInboxCount(...a),
}))

describe("NavBadge", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the Inbox's open count", async () => {
    getInboxCount.mockResolvedValue({ count: 4 })
    renderWithProviders(<NavBadge kind="inbox" />)

    expect(await screen.findByTestId("nav-badge-inbox")).toHaveTextContent("4")
    expect(getInboxCount).toHaveBeenCalledTimes(1)
  })

  it("shows nothing at zero", async () => {
    getInboxCount.mockResolvedValue({ count: 0 })
    renderWithProviders(<NavBadge kind="inbox" />)

    await waitFor(() => expect(getInboxCount).toHaveBeenCalled())
    expect(screen.queryByTestId("nav-badge-inbox")).not.toBeInTheDocument()
  })
})
