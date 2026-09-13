// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The screen credentialing opens on.
 *
 * Bug classes covered:
 *   * asking for a number we already have. She typed her NPI at onboarding;
 *     asking again is the thing that makes setup feel endless.
 *   * "not found" rendering as a failure. The number is probably mistyped, and
 *     telling her the registry is broken sends her to the wrong fix.
 *   * an outage blocking her. The lookup saves typing; it is never a gate.
 *   * the screen writing what the registry said. Confirming is what promotes
 *     it — a lookup that wrote directly would quietly undo the Tier-0 design.
 */

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NpiLookupStep } from "../NpiLookupStep"
import type { NppesLookup } from "@/types/credentialing"

const useNpiLookup = vi.hoisted(() => vi.fn())
const useSettingsUserStatus = vi.hoisted(() => vi.fn())
const saveProfile = vi.hoisted(() => vi.fn())
const recordConfirmation = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useCredentialingChecklist", () => ({
  useNpiLookup: (...args: unknown[]) => useNpiLookup(...args),
  useRecordConfirmation: () => ({ mutate: recordConfirmation, isPending: false }),
}))

vi.mock("@/hooks/useProfessionalInfo", () => ({
  useUpdateProfessionalInfo: () => ({ mutate: saveProfile, isPending: false }),
}))

vi.mock("@/components/settings/useSettingsPreferences", () => ({
  useSettingsUserStatus: () => useSettingsUserStatus(),
}))

vi.mock("../NpiNameSearch", () => ({
  NpiNameSearch: () => <div data-testid="npi-name-search" />,
}))

function found(overrides: Partial<NppesLookup> = {}): NppesLookup {
  return {
    npi: "1999999984",
    found: true,
    legal_name: "TEST THERAPIST",
    credential: "LCSW",
    taxonomy_code: "101YM0800X",
    taxonomy_description: "Mental Health Counselor",
    address_line1: "14 MILL STREET",
    city: "DURHAM",
    state: "NC",
    postal_code: "27701",
    license_number: "MFT001741",
    license_state: "NC",
    active: true,
    entity_type: 1,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  useSettingsUserStatus.mockReturnValue({ data: { npi_number: null }, isLoading: false })
  useNpiLookup.mockReturnValue({ data: undefined, isLoading: false, error: null })
})

describe("when we already have her NPI", () => {
  it("looks it up without asking her to type it again", async () => {
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(), isLoading: false, error: null })

    render(<NpiLookupStep />)

    await waitFor(() => expect(useNpiLookup).toHaveBeenCalledWith("1999999984"))
    expect(screen.getByText("TEST THERAPIST, LCSW")).toBeInTheDocument()
  })

  it("shows what the registry holds, so there is something to confirm", () => {
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(), isLoading: false, error: null })

    render(<NpiLookupStep />)

    expect(screen.getByText(/101YM0800X/)).toBeInTheDocument()
    expect(screen.getByText("14 MILL STREET")).toBeInTheDocument()
  })

  it("says nothing is written until she says so", () => {
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(), isLoading: false, error: null })

    render(<NpiLookupStep />)

    expect(screen.getByText(/never write what the registry says until you say/i)).toBeInTheDocument()
  })

  it("tells its caller which number she confirmed", async () => {
    const user = userEvent.setup()
    const onConfirmed = vi.fn()
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(), isLoading: false, error: null })

    render(<NpiLookupStep onConfirmed={onConfirmed} />)
    await user.click(screen.getByRole("button", { name: /that.s me/i }))

    expect(onConfirmed).toHaveBeenCalledWith("1999999984")
  })

  it("lets her say it is not her and type a different number", async () => {
    const user = userEvent.setup()
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(), isLoading: false, error: null })

    render(<NpiLookupStep />)
    await user.click(screen.getByRole("button", { name: /that.s not me/i }))

    expect(screen.getByLabelText(/your individual npi/i)).toBeInTheDocument()
  })
})

describe("when we do not have it", () => {
  it("asks for it", () => {
    render(<NpiLookupStep />)

    expect(screen.getByLabelText(/your individual npi/i)).toBeInTheDocument()
  })

  it("will not look up something that is not ten digits", async () => {
    const user = userEvent.setup()
    render(<NpiLookupStep />)

    await user.type(screen.getByLabelText(/your individual npi/i), "123")

    expect(screen.getByRole("button", { name: /look it up/i })).toBeDisabled()
  })

  it("refuses non-digits rather than sending them to the registry", async () => {
    const user = userEvent.setup()
    render(<NpiLookupStep />)

    const field = screen.getByLabelText(/your individual npi/i)
    await user.type(field, "19a9999b9984")

    expect(field).toHaveValue("1999999984")
  })
})

describe("when the registry has never heard of the number", () => {
  it("points at the number, not at itself", () => {
    useNpiLookup.mockReturnValue({
      data: found({ found: false }),
      isLoading: false,
      error: null,
    })

    render(<NpiLookupStep />)

    expect(screen.getByText(/couldn.t find 1999999984/i)).toBeInTheDocument()
    expect(screen.getByText(/easy to mistype/i)).toBeInTheDocument()
  })

  it("leaves the field open so she can correct it", () => {
    useNpiLookup.mockReturnValue({
      data: found({ found: false }),
      isLoading: false,
      error: null,
    })

    render(<NpiLookupStep />)

    expect(screen.getByLabelText(/your individual npi/i)).toBeInTheDocument()
  })

  it("does not tell her she is not a real provider", () => {
    useNpiLookup.mockReturnValue({
      data: found({ found: false }),
      isLoading: false,
      error: null,
    })

    render(<NpiLookupStep />)

    expect(screen.getByText(/if it is right, carry on/i)).toBeInTheDocument()
  })
})

describe("when the registry is down", () => {
  it("says so without blaming her", () => {
    useNpiLookup.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("503"),
    })

    render(<NpiLookupStep />)

    expect(screen.getByText(/isn.t answering right now/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing is wrong on your end/i)).toBeInTheDocument()
  })

  it("is never a gate — she can carry on", () => {
    useNpiLookup.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("503"),
    })

    render(<NpiLookupStep />)

    expect(screen.getByText(/you can carry on/i)).toBeInTheDocument()
  })
})

describe("the three doors", () => {
  it("offers a way through for someone who cannot recall her number", () => {
    render(<NpiLookupStep />)

    expect(screen.getByRole("button", { name: /don.t know my npi/i })).toBeInTheDocument()
  })

  it("offers a way through for someone who does not have one at all", () => {
    render(<NpiLookupStep />)

    expect(screen.getByRole("button", { name: /don.t have one/i })).toBeInTheDocument()
  })

  it("does not treat 'no NPI' as an error", async () => {
    const user = userEvent.setup()
    render(<NpiLookupStep />)

    await user.click(screen.getByRole("button", { name: /don.t have one/i }))

    // A licence does not come with an NPI, and plenty of cash-only therapists
    // never applied. The screen is a next step, not a failure.
    expect(screen.getByText(/you.ll need an npi/i)).toBeInTheDocument()
    expect(screen.getByText(/free/i)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /apply at nppes/i })).toHaveAttribute(
      "href",
      "https://nppes.cms.hhs.gov/",
    )
  })

  it("says the rest of her setup is not blocked on it", async () => {
    const user = userEvent.setup()
    render(<NpiLookupStep />)

    await user.click(screen.getByRole("button", { name: /don.t have one/i }))

    expect(screen.getByText(/nothing else in your setup is blocked/i)).toBeInTheDocument()
  })
})

describe("when the registry answers with something that is probably not her", () => {
  it("flags an organisation NPI rather than confirming it", () => {
    // Tier 0 asks for an individual NPI and a billing NPI next to each other,
    // so pasting the practice's number in is an easy mistake. Confirming it
    // silently surfaces months later as a rejected claim.
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({
      data: found({ entity_type: 2 }),
      isLoading: false,
      error: null,
    })

    render(<NpiLookupStep />)

    expect(screen.getByText(/organisation.s npi rather than a person.s/i)).toBeInTheDocument()
  })

  it("flags a deactivated registration", () => {
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({
      data: found({ active: false }),
      isLoading: false,
      error: null,
    })

    render(<NpiLookupStep />)

    expect(screen.getByText(/deactivated/i)).toBeInTheDocument()
  })

  it("still lets her confirm — the registry is often just out of date", async () => {
    const user = userEvent.setup()
    const onConfirmed = vi.fn()
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({
      data: found({ active: false }),
      isLoading: false,
      error: null,
    })

    render(<NpiLookupStep onConfirmed={onConfirmed} />)
    await user.click(screen.getByRole("button", { name: /that.s me/i }))

    expect(onConfirmed).toHaveBeenCalledWith("1999999984")
  })

  it("shows the licence the registry holds, so she need not retype it", () => {
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(), isLoading: false, error: null })

    render(<NpiLookupStep />)

    expect(screen.getByText(/MFT001741/)).toBeInTheDocument()
  })
})

describe("confirming promotes what the registry said", () => {
  function shown(overrides: Partial<NppesLookup> = {}) {
    useSettingsUserStatus.mockReturnValue({
      data: { npi_number: "1999999984" },
      isLoading: false,
    })
    useNpiLookup.mockReturnValue({ data: found(overrides), isLoading: false, error: null })
  }

  it("writes the number, taxonomy and licence onto her record", async () => {
    // She agreed these are hers. Leaving them unsaved would mean the Tier-0
    // cards ask her to type what she just confirmed.
    const user = userEvent.setup()
    shown()

    render(<NpiLookupStep />)
    await user.click(screen.getByRole("button", { name: /that.s me/i }))

    expect(saveProfile).toHaveBeenCalledWith({
      npi_number: "1999999984",
      taxonomy_code: "101YM0800X",
      license_number: "MFT001741",
      license_state: "NC",
    })
  })

  it("sends only what the registry supplied", async () => {
    // An absent taxonomy must not arrive as an instruction to clear the one
    // she already had. Agreeing with a record can never take something away.
    const user = userEvent.setup()
    shown({ taxonomy_code: null, license_number: null, license_state: null })

    render(<NpiLookupStep />)
    await user.click(screen.getByRole("button", { name: /that.s me/i }))

    expect(saveProfile).toHaveBeenCalledWith({ npi_number: "1999999984" })
  })

  it("records the confirmation, with where the value came from", async () => {
    // The provenance row: she was shown this value, by this source, and said
    // yes. It is what makes a later divergence legible rather than mysterious.
    const user = userEvent.setup()
    shown()

    render(<NpiLookupStep />)
    await user.click(screen.getByRole("button", { name: /that.s me/i }))

    expect(recordConfirmation).toHaveBeenCalledWith({
      fieldKey: "npi_number",
      payload: { source: "nppes", confirmed: true, presented_value: "1999999984" },
    })
  })

  it("says so, rather than leaving her wondering whether it took", async () => {
    const user = userEvent.setup()
    shown()

    render(<NpiLookupStep />)
    await user.click(screen.getByRole("button", { name: /that.s me/i }))

    expect(screen.getByRole("button", { name: /saved/i })).toBeInTheDocument()
    expect(screen.getByText(/saved to your record/i)).toBeInTheDocument()
  })

  it("writes nothing until she confirms", () => {
    // The lookup runs on arrival. If merely looking wrote, the promise the
    // screen makes in its own copy would be false.
    shown()

    render(<NpiLookupStep />)

    expect(saveProfile).not.toHaveBeenCalled()
    expect(recordConfirmation).not.toHaveBeenCalled()
  })
})
