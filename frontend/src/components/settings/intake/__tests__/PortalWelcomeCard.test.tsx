// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalWelcomeCard tests — the welcome on the portal's home screen.
 *
 * The preview fills in the practice's name and shows text as text, a save
 * the server refuses says why, and the default is one press away.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PortalWelcomeCard } from "../PortalWelcomeCard"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ApiError } from "@/lib/api/client"

const mockGet = vi.fn()
const mockSave = vi.fn()
const mockReset = vi.fn()

vi.mock("@/lib/api/portalWelcome", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalWelcome")>()),
  getPortalWelcome: (...a: unknown[]) => mockGet(...a),
  savePortalWelcome: (...a: unknown[]) => mockSave(...a),
  resetPortalWelcome: (...a: unknown[]) => mockReset(...a),
}))

const DEFAULT = {
  heading: "Welcome to {practice_name}",
  body: "This is where you'll find what {practice_name} has asked you to do.",
  is_default: true,
  practice_name: "Example Therapy",
  placeholders: [{ name: "practice_name", label: "Practice name" }],
}

describe("PortalWelcomeCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue(DEFAULT)
    mockSave.mockImplementation((draft: { heading: string; body: string }) => {
      const saved = { ...DEFAULT, ...draft, is_default: false }
      mockGet.mockResolvedValue(saved)
      return Promise.resolve(saved)
    })
  })

  it("starts on the default, previewed with the practice's name", async () => {
    renderWithProviders(<PortalWelcomeCard />)
    expect(await screen.findByLabelText("Heading")).toHaveValue("Welcome to {practice_name}")
    const preview = screen.getByTestId("portal-welcome-card-preview")
    expect(preview).toHaveTextContent("Welcome to Example Therapy")
    expect(preview).toHaveTextContent("what Example Therapy has asked you to do")
    expect(screen.queryByRole("button", { name: "Use the default" })).not.toBeInTheDocument()
  })

  it("shows markup literally rather than rendering it", async () => {
    renderWithProviders(<PortalWelcomeCard />)
    const body = await screen.findByLabelText("Message")
    fireEvent.change(body, { target: { value: "Hello <b>x</b>" } })

    const preview = screen.getByTestId("portal-welcome-card-preview")
    expect(preview).toHaveTextContent("Hello <b>x</b>")
    expect(preview.querySelector("b")).toBeNull()
  })

  it("inserts the practice name where the cursor is and saves", async () => {
    const user = userEvent.setup()
    renderWithProviders(<PortalWelcomeCard />)
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement

    fireEvent.change(body, { target: { value: "Hi from ." } })
    body.setSelectionRange(8, 8)
    await user.click(screen.getByRole("button", { name: "Practice name" }))
    expect(body.value).toBe("Hi from {practice_name}.")
    expect(screen.getByTestId("portal-welcome-card-preview")).toHaveTextContent(
      "Hi from Example Therapy.",
    )

    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(mockSave).toHaveBeenCalledWith({
        heading: "Welcome to {practice_name}",
        body: "Hi from {practice_name}.",
      }),
    )
    expect(await screen.findByText("Saved")).toBeInTheDocument()
  })

  it("says what the server refused", async () => {
    const user = userEvent.setup()
    mockSave.mockRejectedValue(
      new ApiError("PORTAL_WELCOME_INVALID", "invalid", {
        problems: ["Remove the placeholders the welcome cannot fill in: {client_name}."],
      }),
    )
    renderWithProviders(<PortalWelcomeCard />)
    fireEvent.change(await screen.findByLabelText("Message"), {
      target: { value: "Hello {client_name}" },
    })
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByTestId("portal-welcome-problems")).toHaveTextContent(
      "{client_name}",
    )
  })

  it("goes back to the default", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue({ ...DEFAULT, heading: "Custom", is_default: false })
    mockReset.mockResolvedValue(DEFAULT)
    renderWithProviders(<PortalWelcomeCard />)

    await user.click(await screen.findByRole("button", { name: "Use the default" }))
    expect(mockReset).toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByLabelText("Heading")).toHaveValue("Welcome to {practice_name}"),
    )
  })
})
