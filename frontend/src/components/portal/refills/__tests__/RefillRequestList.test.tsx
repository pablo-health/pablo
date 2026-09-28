// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RefillRequestList: each stored status reads as its own label, dated by
 * when it got there, with one line on what happens next; an empty list
 * says so in one line.
 */

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import type { RefillRequest, RefillRequestStatus } from "@/lib/api/patientRefills"
import { RefillRequestList } from "../RefillRequestList"
import { NEXT_STEPS } from "../refillsCopy"

function request(
  id: string,
  status: RefillRequestStatus,
  decidedAt: string | null = status === "requested" ? null : "2026-09-10T15:00:00Z",
): RefillRequest {
  return {
    id,
    medication_id: null,
    medication_text: `Medication ${id}`,
    pharmacy_text: null,
    patient_note: null,
    status,
    created_at: "2026-09-03T15:00:00Z",
    decided_at: decidedAt,
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

  it("shows the medication, in the order given", () => {
    render(
      <RefillRequestList requests={[request("new", "requested"), request("old", "approved")]} />,
    )

    const items = screen.getAllByRole("listitem")
    expect(items.map((item) => item.getAttribute("data-testid"))).toEqual([
      "portal-refills-request-new",
      "portal-refills-request-old",
    ])
    expect(items[0].textContent).toContain("Medication new")
  })

  it("dates a waiting request by when it was sent", () => {
    render(<RefillRequestList requests={[request("r1", "requested")]} />)

    expect(screen.getByTestId("portal-refills-date-r1").textContent).toBe("Sep 3, 2026")
  })

  it.each(["approved", "needs_visit", "declined"] as const)(
    "dates a %s request by when it was decided",
    (status) => {
      render(<RefillRequestList requests={[request("r1", status)]} />)

      expect(screen.getByTestId("portal-refills-date-r1").textContent).toBe("Sep 10, 2026")
    },
  )

  it("falls back to the sent date when a decision time is missing", () => {
    render(<RefillRequestList requests={[request("r1", "approved", null)]} />)

    expect(screen.getByTestId("portal-refills-date-r1").textContent).toBe("Sep 3, 2026")
  })

  it.each([
    ["requested", "Your prescriber will look at this."],
    ["approved", "Check with your pharmacy before you go."],
    ["needs_visit", "Bring this up at your next appointment."],
    ["declined", "Message your practice if you have questions."],
  ] as const)("tells a %s request what happens next", (status, line) => {
    render(<RefillRequestList requests={[request("r1", status)]} messagingEnabled />)

    expect(screen.getByTestId("portal-refills-next-r1").textContent).toBe(line)
  })

  it("leaves the messaging line off a declined request when messaging is off", () => {
    render(
      <RefillRequestList
        requests={[request("r1", "declined"), request("r2", "needs_visit")]}
        messagingEnabled={false}
      />,
    )

    expect(screen.queryByTestId("portal-refills-next-r1")).toBeNull()
    // Control: the other lines do not depend on messaging.
    expect(screen.getByTestId("portal-refills-next-r2").textContent).toBe(NEXT_STEPS.needs_visit)
  })

  it("colors approved green and needs_visit amber, and keeps declined neutral", () => {
    render(
      <RefillRequestList
        requests={[
          request("a", "approved"),
          request("v", "needs_visit"),
          request("d", "declined"),
          request("r", "requested"),
        ]}
      />,
    )

    expect(screen.getByTestId("portal-refills-status-a").className).toContain("bg-green-100")
    expect(screen.getByTestId("portal-refills-status-v").className).toContain("bg-amber-100")
    expect(screen.getByTestId("portal-refills-status-d").className).toContain("bg-neutral-100")
    expect(screen.getByTestId("portal-refills-status-d").className).not.toContain("red")
    expect(screen.getByTestId("portal-refills-status-r").className).toContain("bg-neutral-100")
  })

  it("marks only the highlighted row and shows the notice above the rows", () => {
    render(
      <RefillRequestList
        requests={[request("new", "requested"), request("old", "approved")]}
        highlightId="new"
        notice={<p data-testid="notice">Sent</p>}
      />,
    )

    expect(screen.getByTestId("portal-refills-request-new").getAttribute("data-highlighted")).toBe(
      "true",
    )
    expect(
      screen.getByTestId("portal-refills-request-old").getAttribute("data-highlighted"),
    ).toBeNull()
    const notice = screen.getByTestId("notice")
    const list = screen.getByRole("list")
    expect(notice.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("says so in one line when there are no requests", () => {
    render(<RefillRequestList requests={[]} />)

    expect(screen.getByTestId("portal-refills-list-empty").textContent).toBe(
      "You haven't asked for a refill yet.",
    )
    expect(screen.queryByRole("list")).toBeNull()
  })
})
