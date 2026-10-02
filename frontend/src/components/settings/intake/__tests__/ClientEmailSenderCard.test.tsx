// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClientEmailSenderCard tests — who client email is from.
 *
 * The preview line follows the form, shows the practice's own domain once it
 * can send and the deployment's address until then, a save the server refuses
 * says why, and a clinician who does not own the practice reads but cannot
 * change anything.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ClientEmailSenderCard } from "../ClientEmailSenderCard"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ApiError } from "@/lib/api/client"
import type { EmailSender } from "@/lib/api/emailSender"

const mockGet = vi.fn()
const mockSave = vi.fn()

vi.mock("@/lib/api/emailSender", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/emailSender")>()),
  getEmailSender: (...a: unknown[]) => mockGet(...a),
  saveEmailSender: (...a: unknown[]) => mockSave(...a),
}))

function sender(overrides: Partial<EmailSender> = {}): EmailSender {
  return {
    can_edit: true,
    applies: true,
    chosen: { sender_name: null, sender_local_part: null, reply_to: null },
    defaults: {
      sender_name: "Example Therapy",
      sender_local_part: "portal",
      reply_to: "owner@example.com",
    },
    sending_domain: null,
    deployment_from_address: "mail@deploy.example.net",
    effective: {
      from_name: "Example Therapy",
      from_address: null,
      reply_to: "owner@example.com",
    },
    ...overrides,
  }
}

function preview(): HTMLElement {
  return screen.getByTestId("client-email-preview")
}

describe("ClientEmailSenderCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("previews the deployment's address under the practice's name while no domain can send", async () => {
    mockGet.mockResolvedValue(sender())
    renderWithProviders(<ClientEmailSenderCard />)

    expect(await screen.findByLabelText("Sender name")).toHaveValue("")
    expect(preview()).toHaveTextContent(
      "Clients see: Example Therapy <mail@deploy.example.net> · replies go to owner@example.com",
    )
    expect(screen.getByTestId("client-email-pending-note")).toHaveTextContent(
      "Once your domain's email is verified, this switches to portal@your domain automatically.",
    )
  })

  it("follows the form on the practice's verified domain", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue(
      sender({
        sending_domain: "example.com",
        effective: {
          from_name: "Example Therapy",
          from_address: "portal@example.com",
          reply_to: "owner@example.com",
        },
      }),
    )
    renderWithProviders(<ClientEmailSenderCard />)

    expect(await screen.findByText("@example.com")).toBeInTheDocument()
    expect(preview()).toHaveTextContent("Clients see: Example Therapy <portal@example.com>")
    expect(screen.queryByTestId("client-email-pending-note")).not.toBeInTheDocument()

    await user.type(screen.getByLabelText("Sender name"), "Jordan Rivera, LCSW")
    await user.type(screen.getByLabelText("Address on your domain"), "Hello")
    await user.type(screen.getByLabelText("Replies go to"), "frontdesk@example.com")

    expect(preview()).toHaveTextContent(
      "Clients see: Jordan Rivera, LCSW <hello@example.com> · replies go to frontdesk@example.com",
    )
  })

  it("saves the three fields, blank ones as their defaults", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue(sender())
    mockSave.mockImplementation((fields: EmailSender["chosen"]) => {
      const saved = sender({ chosen: fields })
      mockGet.mockResolvedValue(saved)
      return Promise.resolve(saved)
    })
    renderWithProviders(<ClientEmailSenderCard />)

    const save = await screen.findByRole("button", { name: "Save" })
    expect(save).toBeDisabled()
    await user.type(screen.getByLabelText("Sender name"), "  Jordan Rivera, LCSW ")
    await user.click(save)

    expect(mockSave.mock.calls[0][0]).toEqual({
      sender_name: "Jordan Rivera, LCSW",
      sender_local_part: null,
      reply_to: null,
    })
    expect(await screen.findByText("Saved")).toBeInTheDocument()
  })

  it("says why the server refused a save", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue(sender())
    mockSave.mockRejectedValue(
      new ApiError(
        "EMAIL_SENDER_INVALID",
        "Clients can reply to these emails, so pick a name that invites it, like portal or hello.",
        undefined,
        422,
      ),
    )
    renderWithProviders(<ClientEmailSenderCard />)

    await user.type(await screen.findByLabelText("Address on your domain"), "noreply")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Clients can reply to these emails, so pick a name that invites it, like portal or hello.",
    )
  })

  it("is read-only for a clinician who does not own the practice", async () => {
    mockGet.mockResolvedValue(sender({ can_edit: false }))
    renderWithProviders(<ClientEmailSenderCard />)

    expect(await screen.findByLabelText("Sender name")).toHaveAttribute("readonly")
    expect(screen.getByLabelText("Replies go to")).toHaveAttribute("readonly")
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
    expect(screen.getByText("Only the practice owner can change this.")).toBeInTheDocument()
  })

  it("offers no form where the deployment cannot send as the practice", async () => {
    mockGet.mockResolvedValue(sender({ applies: false }))
    renderWithProviders(<ClientEmailSenderCard />)

    expect(await screen.findByTestId("client-email-not-applicable")).toBeInTheDocument()
    expect(screen.queryByLabelText("Sender name")).not.toBeInTheDocument()
    await waitFor(() => expect(screen.queryByTestId("client-email-preview")).toBeNull())
  })
})
