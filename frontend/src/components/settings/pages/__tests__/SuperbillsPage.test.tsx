// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SuperbillsPage tests — pick a client and a period, generate, download.
 *
 * What matters here: the request carries exactly what was picked, a PDF
 * answer is handed to the browser as a download named like the route names
 * it, and a refusal is shown as the list of findings the route sent — each
 * with the field it lives in — rather than a generic failure.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { SuperbillsPage } from "../SuperbillsPage"
import { SuperbillRefusedError } from "@/lib/api/superbills"

const mockUsePatientList = vi.fn()
const mockFetchSuperbill = vi.fn()

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: (...args: unknown[]) => mockUsePatientList(...args),
}))

vi.mock("@/lib/api/superbills", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/superbills")>("@/lib/api/superbills")
  return {
    ...actual,
    fetchSuperbill: (...args: unknown[]) => mockFetchSuperbill(...args),
  }
})

const PATIENTS = {
  data: [
    { id: "patient-1", first_name: "John", last_name: "Anon" },
    { id: "patient-2", first_name: "Jane", last_name: "Other" },
  ],
  total: 2,
  page: 1,
  page_size: 100,
}

async function fillAndGenerate(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText("Client"), "patient-1")
  await user.type(screen.getByLabelText("From"), "2026-09-01")
  await user.type(screen.getByLabelText("To"), "2026-09-30")
  await user.click(screen.getByRole("button", { name: /Generate PDF/ }))
}

describe("SuperbillsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePatientList.mockReturnValue({ data: PATIENTS, isLoading: false })
    globalThis.URL.createObjectURL = vi.fn(() => "blob:superbill")
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  it("lists the practice's clients to pick from", () => {
    render(<SuperbillsPage />)

    expect(screen.getByRole("option", { name: "Anon, John" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Other, Jane" })).toBeInTheDocument()
  })

  it("keeps Generate disabled until a client and both dates are picked", async () => {
    const user = userEvent.setup()
    render(<SuperbillsPage />)
    const button = screen.getByRole("button", { name: /Generate PDF/ })

    expect(button).toBeDisabled()
    await user.selectOptions(screen.getByLabelText("Client"), "patient-1")
    await user.type(screen.getByLabelText("From"), "2026-09-01")
    expect(button).toBeDisabled()
    await user.type(screen.getByLabelText("To"), "2026-09-30")
    expect(button).toBeEnabled()
  })

  it("requests the picked client and period, and downloads the PDF under the route's name", async () => {
    const user = userEvent.setup()
    mockFetchSuperbill.mockResolvedValue(new Blob(["%PDF"], { type: "application/pdf" }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    render(<SuperbillsPage />)

    await fillAndGenerate(user)

    await waitFor(() =>
      expect(mockFetchSuperbill).toHaveBeenCalledWith("patient-1", "2026-09-01", "2026-09-30"),
    )
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(screen.getByRole("status")).toHaveTextContent("Downloaded superbill-2026-09-01-to-2026-09-30.pdf")
    click.mockRestore()
  })

  it("shows every finding of a refusal with the field it lives in", async () => {
    const user = userEvent.setup()
    mockFetchSuperbill.mockRejectedValue(
      new SuperbillRefusedError("The superbill is missing required information.", [
        {
          severity: "blocking",
          code: "missing_field",
          message: "rendering_provider.npi is required.",
          field: "rendering_provider.npi",
        },
        {
          severity: "blocking",
          code: "visit_without_claim",
          message: "The visit on 2026-09-08 has no claim built from it. Build one from the session, then generate the superbill.",
          field: "appointments[abc]",
        },
      ]),
    )
    render(<SuperbillsPage />)

    await fillAndGenerate(user)

    expect(await screen.findByText("The superbill was not generated")).toBeInTheDocument()
    const items = screen.getAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent("rendering_provider.npi is required.")
    expect(items[0]).toHaveTextContent("rendering_provider.npi")
    expect(items[1]).toHaveTextContent("The visit on 2026-09-08 has no claim built from it.")
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("shows an ordinary failure as such, not as a refusal", async () => {
    const user = userEvent.setup()
    mockFetchSuperbill.mockRejectedValue(new Error("API request failed with status 500"))
    render(<SuperbillsPage />)

    await fillAndGenerate(user)

    expect(await screen.findByText("Something went wrong")).toBeInTheDocument()
    expect(screen.getByText("API request failed with status 500")).toBeInTheDocument()
    expect(screen.queryByRole("list", { name: "What is missing" })).not.toBeInTheDocument()
  })
})
