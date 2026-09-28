// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * InviteEmailCard tests — the practice's invitation wording.
 *
 * The editor exists only where the email channel sends what it says, the
 * preview beside it is the server's rendering, and a draft the server would
 * refuse cannot be saved.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { InviteEmailCard } from "../InviteEmailCard"
import { renderWithProviders } from "@/test/renderWithProviders"

const mockGet = vi.fn()
const mockSave = vi.fn()
const mockReset = vi.fn()
const mockPreview = vi.fn()

vi.mock("@/lib/api/inviteTemplate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/inviteTemplate")>()),
  getInviteTemplate: (...a: unknown[]) => mockGet(...a),
  saveInviteTemplate: (...a: unknown[]) => mockSave(...a),
  resetInviteTemplate: (...a: unknown[]) => mockReset(...a),
  previewInviteTemplate: (...a: unknown[]) => mockPreview(...a),
}))

const PLACEHOLDERS = [
  { name: "portal_link", label: "Sign-in link", required: true },
  { name: "client_first_name", label: "Client's first name", required: false },
]

const DEFAULT = {
  editable: true,
  subject: "Your sign-in link",
  body: "Use this link to sign in:\n\n{{portal_link}}",
  is_default: true,
  placeholders: PLACEHOLDERS,
}

describe("InviteEmailCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue(DEFAULT)
    mockPreview.mockImplementation((draft: { subject: string; body: string }) =>
      Promise.resolve({
        subject: draft.subject,
        text: draft.body.replace("{{portal_link}}", "[personal sign-in link]").replace("{{client_first_name}}", "Alex"),
        problems: draft.body.includes("{{portal_link}}")
          ? []
          : ["Include {{portal_link}} in the message, so the client can sign in."],
      }),
    )
    mockSave.mockImplementation((draft: { subject: string; body: string }) => {
      const saved = { ...DEFAULT, ...draft, is_default: false }
      mockGet.mockResolvedValue(saved)
      return Promise.resolve(saved)
    })
  })

  it("renders nothing where the wording is fixed", async () => {
    mockGet.mockResolvedValue({ ...DEFAULT, editable: false })
    const { container } = renderWithProviders(<InviteEmailCard />)
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("previews the wording for an example client", async () => {
    renderWithProviders(<InviteEmailCard />)
    const preview = await screen.findByTestId("invite-email-card-preview")
    await waitFor(() => expect(preview).toHaveTextContent("[personal sign-in link]"))
  })

  it("inserts a placeholder where the cursor is and saves the new wording", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InviteEmailCard />)
    const body = (await screen.findByLabelText("Message")) as HTMLTextAreaElement

    fireEvent.change(body, { target: { value: "Hi ,\n{{portal_link}}" } })
    body.setSelectionRange(3, 3)
    await user.click(screen.getByRole("button", { name: "Client's first name" }))
    expect(body.value).toBe("Hi {{client_first_name}},\n{{portal_link}}")

    await waitFor(() =>
      expect(screen.getByTestId("invite-email-card-preview")).toHaveTextContent("Hi Alex,"),
    )
    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(mockSave).toHaveBeenCalledWith({
        subject: "Your sign-in link",
        body: "Hi {{client_first_name}},\n{{portal_link}}",
      }),
    )
    // The first save turns the wording from the default into the practice's
    // own; the editor keeps what was typed and says it is saved.
    expect(await screen.findByText("Saved")).toBeInTheDocument()
    expect(body.value).toBe("Hi {{client_first_name}},\n{{portal_link}}")
  })

  it("will not save a message without the sign-in link, and says why", async () => {
    renderWithProviders(<InviteEmailCard />)
    const body = await screen.findByLabelText("Message")
    fireEvent.change(body, { target: { value: "Come and see us." } })

    expect(await screen.findByTestId("invite-email-problems")).toHaveTextContent(
      "Include {{portal_link}}",
    )
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("goes back to the standard wording", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue({ ...DEFAULT, subject: "Custom", is_default: false })
    mockReset.mockResolvedValue(DEFAULT)
    renderWithProviders(<InviteEmailCard />)

    await user.click(await screen.findByRole("button", { name: "Use the standard wording" }))
    expect(mockReset).toHaveBeenCalled()
  })
})
