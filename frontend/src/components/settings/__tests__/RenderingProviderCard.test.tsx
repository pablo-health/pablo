// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RenderingProviderCard tests — the clinician's own NPI and taxonomy code.
 *
 * The taxonomy picker offers the common behavioral-health codes and a free
 * text entry for any other; a stored code outside the list opens as free
 * text with the code in it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RenderingProviderCard } from "../RenderingProviderCard"

const mockUpdate = vi.fn()

vi.mock("@/hooks/useProfessionalInfo", () => ({
  useUpdateProfessionalInfo: () => ({ mutate: mockUpdate, isPending: false }),
}))

describe("RenderingProviderCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("picks a common code and saves it", async () => {
    const user = userEvent.setup()
    render(<RenderingProviderCard npiNumber="1234567893" taxonomyCode={null} />)

    await user.click(screen.getByRole("combobox", { name: /taxonomy code/i }))
    await user.click(screen.getByRole("option", { name: /Clinical Social Worker/ }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith({ taxonomy_code: "1041C0700X" }, expect.anything())
  })

  it("takes any other code as free text, upper-cased", async () => {
    const user = userEvent.setup()
    render(<RenderingProviderCard npiNumber="1234567893" taxonomyCode={null} />)

    await user.click(screen.getByRole("combobox", { name: /taxonomy code/i }))
    await user.click(screen.getByRole("option", { name: "Another code" }))
    await user.type(screen.getByRole("textbox", { name: "Taxonomy code" }), "106h00000x")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockUpdate).toHaveBeenCalledWith({ taxonomy_code: "106H00000X" }, expect.anything())
  })

  it("opens a stored code outside the list as free text", () => {
    render(<RenderingProviderCard npiNumber={null} taxonomyCode="106H00000X" />)

    expect(screen.getByRole("textbox", { name: "Taxonomy code" })).toHaveValue("106H00000X")
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })

  it("saves the clinician's own NPI and refuses one that is not ten digits", async () => {
    const user = userEvent.setup()
    render(<RenderingProviderCard npiNumber={null} taxonomyCode="103T00000X" />)

    await user.type(screen.getByLabelText("Your NPI"), "123")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(screen.getByRole("alert")).toHaveTextContent("ten digits")
    expect(mockUpdate).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText("Your NPI"), "4567893")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(mockUpdate).toHaveBeenCalledWith({ npi_number: "1234567893" }, expect.anything())
  })
})
