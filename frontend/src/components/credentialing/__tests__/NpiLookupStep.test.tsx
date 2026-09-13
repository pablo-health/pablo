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

vi.mock("@/hooks/useCredentialingChecklist", () => ({
  useNpiLookup: (...args: unknown[]) => useNpiLookup(...args),
}))

vi.mock("@/components/settings/useSettingsPreferences", () => ({
  useSettingsUserStatus: () => useSettingsUserStatus(),
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

  it("hands the confirmed number to its caller rather than saving it itself", async () => {
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
