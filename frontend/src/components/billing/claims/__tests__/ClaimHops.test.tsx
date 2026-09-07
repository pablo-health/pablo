// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The timeline is the receipt ledger, oldest first: a receipt that changed
 * the claim's state is a hop, and one that left it where it was is a note.
 */

import { describe, expect, it } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { ClaimHops } from "../ClaimHops"
import { receipt } from "./claimFixtures"

const LEDGER = [
  receipt("rejected", {
    id: "r-5",
    from_state: "ch_accepted",
    to_state: "rejected",
    occurred_at: "2026-09-05T09:00:00Z",
    detail: { codes: [{ system: "status", code: "A3:21" }] },
  }),
  receipt("submitted", {
    id: "r-1",
    from_state: "validated",
    to_state: "submitted",
    occurred_at: "2026-09-01T09:00:00Z",
    vendor_transaction_id: "txn-7712",
  }),
  receipt("acknowledged", {
    id: "r-3",
    from_state: "ch_accepted",
    to_state: "ch_accepted",
    occurred_at: "2026-09-03T09:00:00Z",
  }),
  receipt("ch_accepted", {
    id: "r-2",
    from_state: "submitted",
    to_state: "ch_accepted",
    occurred_at: "2026-09-02T09:00:00Z",
  }),
  receipt("status_checked", {
    id: "r-4",
    from_state: "ch_accepted",
    to_state: "ch_accepted",
    occurred_at: "2026-09-04T09:00:00Z",
  }),
]

describe("ClaimHops", () => {
  it("renders the state-moving receipts as hops and the rest as notes", () => {
    render(<ClaimHops receipts={LEDGER} />)
    expect(screen.getAllByTestId("claim-hop").map((el) => el.dataset.kind)).toEqual([
      "submitted",
      "ch_accepted",
      "rejected",
    ])
    expect(screen.getAllByTestId("claim-note").map((el) => el.dataset.kind)).toEqual([
      "acknowledged",
      "status_checked",
    ])
  })

  it("orders the whole ledger oldest first, whatever order it arrives in", () => {
    render(<ClaimHops receipts={LEDGER} />)
    const entries = within(screen.getByTestId("claim-hops")).getAllByRole("listitem")
    expect(entries.map((el) => el.dataset.kind)).toEqual([
      "submitted",
      "ch_accepted",
      "acknowledged",
      "status_checked",
      "rejected",
    ])
  })

  it("shows each entry's moment, and the vendor ids and codes it carries", () => {
    render(<ClaimHops receipts={LEDGER} />)
    expect(screen.getAllByText(/2026, \d+:\d\d/)).toHaveLength(LEDGER.length)
    expect(screen.getByText("txn-7712")).toBeInTheDocument()
    expect(screen.getByText("status:A3:21")).toBeInTheDocument()
  })

  it("says so when nothing has happened to the claim yet", () => {
    render(<ClaimHops receipts={[]} />)
    expect(screen.getByText("The claim has not left the practice yet.")).toBeInTheDocument()
    expect(screen.queryByTestId("claim-hop")).not.toBeInTheDocument()
  })
})
