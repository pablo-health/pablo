// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Licensed instruments card in Settings > Patient portal.
 *
 * Two lists, and the difference between them is the point. A measure whose
 * USE is restricted gets a box to tick, because ticking it is what lets the
 * form builder offer it. A measure that is sold gets a line saying what to
 * do instead, and no control at all — an offer that would do nothing is
 * worse than no offer.
 *
 * The claim the box makes is asserted verbatim. It is the whole of what a
 * practice is saying, and a sentence that grew a legal clause would be a
 * change to what somebody agreed to rather than a change to the wording.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { LicensedInstrumentsCard } from "../LicensedInstrumentsCard"
import {
  LICENSED_CHECKBOX,
  LICENSED_ON_FILE,
  LICENSED_SAVE,
  LICENSED_WITHDRAW,
  LICENSE_REFERENCE_LABEL,
  SOLD_GUIDANCE,
} from "../intakeCopy"
import type { Instrument } from "@/types/instruments"

const mockUseInstruments = vi.fn()
const mockAttest = vi.fn()
const mockWithdraw = vi.fn()

vi.mock("@/hooks/useInstruments", () => ({
  useInstruments: () => mockUseInstruments(),
  useAttestInstrument: () => ({ mutate: mockAttest, isPending: false, error: null }),
  useRevokeInstrumentAttestation: () => ({
    mutate: mockWithdraw,
    isPending: false,
    error: null,
  }),
}))

const FREE: Instrument = {
  code: "phq9",
  display_name: "PHQ-9",
  rights: "public_domain",
  rights_note: "No permission needed.",
  publisher_url: "https://www.phqscreeners.com",
  item_count: 9,
  can_ask_on_a_form: true,
  attested: false,
  attested_at: null,
  license_reference: null,
}

const RESTRICTED: Instrument = {
  code: "cssrs",
  display_name: "C-SSRS",
  rights: "attestation_required",
  rights_note: "Free for clinical and community use.",
  publisher_url: "https://cssrs.columbia.edu",
  item_count: 6,
  can_ask_on_a_form: false,
  attested: false,
  attested_at: null,
  license_reference: null,
}

const SOLD: Instrument = {
  code: "bdi2",
  display_name: "BDI-II",
  rights: "never_ship",
  rights_note: "Sold by Pearson under a per-use license.",
  publisher_url: "https://www.pearsonassessments.com",
  item_count: 21,
  can_ask_on_a_form: false,
  attested: false,
  attested_at: null,
  license_reference: null,
}

function withInstruments(instruments: Instrument[]) {
  mockUseInstruments.mockReturnValue({ data: instruments })
  return render(<LicensedInstrumentsCard />)
}

describe("LicensedInstrumentsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("shows a restricted measure with the publisher's own line", () => {
    withInstruments([RESTRICTED])

    expect(screen.getByText("C-SSRS")).toBeInTheDocument()
    expect(screen.getByText(/Free for clinical and community use/)).toBeInTheDocument()
    // An exact name rather than a pattern: the link's text is the URL with
    // its scheme stripped, so there is nothing to match loosely.
    expect(screen.getByRole("link", { name: "cssrs.columbia.edu" })).toHaveAttribute(
      "href",
      "https://cssrs.columbia.edu"
    )
  })

  it("leaves measures that need no permission off the screen", () => {
    withInstruments([FREE, RESTRICTED])

    expect(screen.queryByText("PHQ-9")).not.toBeInTheDocument()
  })

  it("makes the practice tick the box before it can save", async () => {
    const user = userEvent.setup()
    withInstruments([RESTRICTED])

    expect(screen.getByRole("button", { name: LICENSED_SAVE })).toBeDisabled()

    await user.click(screen.getByLabelText(LICENSED_CHECKBOX))

    expect(screen.getByRole("button", { name: LICENSED_SAVE })).toBeEnabled()
  })

  it("records the permission with the practice's own reference", async () => {
    const user = userEvent.setup()
    withInstruments([RESTRICTED])

    await user.click(screen.getByLabelText(LICENSED_CHECKBOX))
    await user.type(screen.getByLabelText(LICENSE_REFERENCE_LABEL), "PO-4417")
    await user.click(screen.getByRole("button", { name: LICENSED_SAVE }))

    expect(mockAttest).toHaveBeenCalledWith({
      code: "cssrs",
      input: { license_reference: "PO-4417" },
    })
  })

  it("sends no reference when the practice has none to give", async () => {
    const user = userEvent.setup()
    withInstruments([RESTRICTED])

    await user.click(screen.getByLabelText(LICENSED_CHECKBOX))
    await user.click(screen.getByRole("button", { name: LICENSED_SAVE }))

    expect(mockAttest).toHaveBeenCalledWith({
      code: "cssrs",
      input: { license_reference: null },
    })
  })

  it("shows what is on file instead of the box once it is recorded", () => {
    withInstruments([{ ...RESTRICTED, attested: true, license_reference: "PO-4417" }])

    expect(screen.getByText(LICENSED_ON_FILE)).toBeInTheDocument()
    expect(screen.getByText("PO-4417")).toBeInTheDocument()
    expect(screen.queryByLabelText(LICENSED_CHECKBOX)).not.toBeInTheDocument()
  })

  it("withdraws the permission in force", async () => {
    const user = userEvent.setup()
    withInstruments([{ ...RESTRICTED, attested: true }])

    await user.click(screen.getByRole("button", { name: LICENSED_WITHDRAW }))

    expect(mockWithdraw).toHaveBeenCalledWith("cssrs")
  })

  it("offers a sold measure no control, and says what to do instead", () => {
    withInstruments([SOLD])

    expect(screen.getByText("BDI-II")).toBeInTheDocument()
    expect(screen.getByText(SOLD_GUIDANCE)).toBeInTheDocument()
    expect(screen.queryByLabelText(LICENSED_CHECKBOX)).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: LICENSED_SAVE })).not.toBeInTheDocument()
  })

  it("claims nothing beyond holding the permission", () => {
    withInstruments([RESTRICTED])

    expect(screen.getByText(LICENSED_CHECKBOX).textContent).toBe(
      "Our practice holds the permission required to use this instrument"
    )
  })
})
