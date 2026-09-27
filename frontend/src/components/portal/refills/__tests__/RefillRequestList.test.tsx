// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RefillRequestList: each stored status reads as its own label, and an
 * empty list says so in one line.
 */

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import type { RefillRequest, RefillRequestStatus } from "@/lib/api/patientRefills"
import { RefillRequestList } from "../RefillRequestList"

function request(id: string, status: RefillRequestStatus): RefillRequest {
  return {
    id,
    medication_id: null,
    medication_text: `Medication ${id}`,
    pharmacy_text: null,
    patient_note: null,
    status,
    created_at: "2026-09-03T15:00:00Z",
    decided_at: null,
  }
}

describe("RefillRequestList", () => {
  it.each([
    ["requested", "Received"],
    ["approved", "Sent to your pharmacy"],
    ["needs_visit", "Let's talk at your next visit"],
    ["declined", "Not refilled"],
  ] as const)("labels %s as %s", (status, label) => {
    render(<RefillRequestList requests={[request("r1", status)]} />)

    expect(screen.getByTestId("portal-refills-status-r1").textContent).toBe(label)
  })

  it("shows the medication and the date it was sent, in the order given", () => {
    render(
      <RefillRequestList requests={[request("new", "requested"), request("old", "approved")]} />,
    )

    const items = screen.getAllByRole("listitem")
    expect(items.map((item) => item.getAttribute("data-testid"))).toEqual([
      "portal-refills-request-new",
      "portal-refills-request-old",
    ])
    expect(items[0].textContent).toContain("Medication new")
    expect(items[0].textContent).toContain("Sep 3, 2026")
  })

  it("says so in one line when there are no requests", () => {
    render(<RefillRequestList requests={[]} />)

    expect(screen.getByTestId("portal-refills-list-empty").textContent).toBe(
      "You haven't asked for a refill yet.",
    )
    expect(screen.queryByRole("list")).toBeNull()
  })
})
