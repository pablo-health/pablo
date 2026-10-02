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
    },
    sending_domain: null,
    deployment_from_address: "mail@deploy.example.net",
    effective: {
      from_name: "Example Therapy",
      from_address: null,
      reply_to: null,
    },
    ...overrides,
  }
}

function preview(): HTMLElement {
  return screen.getByTestId("client-email-preview")
}

function note(): HTMLElement {
  return screen.getByTestId("client-email-pending-note")
}

describe("ClientEmailSenderCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("with nothing set, previews the deployment's address and names no reply-to", async () => {
    mockGet.mockResolvedValue(sender())
    renderWithProviders(<ClientEmailSenderCard />)

    const replyTo = await screen.findByLabelText("Replies go to")
    expect(replyTo).toHaveValue("")
    expect(replyTo).not.toHaveAttribute("placeholder")
    expect(preview()).toHaveTextContent("Clients see: Example Therapy <mail@deploy.example.net>")
    expect(preview()).not.toHaveTextContent("replies go to")
    expect(note()).toHaveTextContent(
      "Emails come from portal@your domain once its email is verified and a reply-to address is set.",
    )
  })

  it("with a verified domain but no reply-to, stays on the deployment's address and says why", async () => {
    mockGet.mockResolvedValue(sender({ sending_domain: "example.com" }))
    renderWithProviders(<ClientEmailSenderCard />)

    expect(await screen.findByText("@example.com")).toBeInTheDocument()
    expect(preview()).toHaveTextContent("Clients see: Example Therapy <mail@deploy.example.net>")
    expect(note()).toHaveTextContent(
      "Emails come from portal@example.com once a reply-to address is set.",
    )
  })

  it("switches to the practice's domain in the preview as soon as a reply-to is typed", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue(sender({ sending_domain: "example.com" }))
    renderWithProviders(<ClientEmailSenderCard />)

    await user.type(await screen.findByLabelText("Sender name"), "Jordan Rivera, LCSW")
    await user.type(screen.getByLabelText("Address on your domain"), "Hello")
    await user.type(screen.getByLabelText("Replies go to"), "frontdesk@example.com")

    expect(preview()).toHaveTextContent(
      "Clients see: Jordan Rivera, LCSW <hello@example.com> · replies go to frontdesk@example.com",
    )
    expect(screen.queryByTestId("client-email-pending-note")).not.toBeInTheDocument()
  })

  it("shows the server's answer once a reply-to is saved on a verified domain", async () => {
    mockGet.mockResolvedValue(
      sender({
        sending_domain: "example.com",
        chosen: { sender_name: null, sender_local_part: null, reply_to: "frontdesk@example.com" },
        effective: {
          from_name: "Example Therapy",
          from_address: "portal@example.com",
          reply_to: "frontdesk@example.com",
        },
      }),
    )
    renderWithProviders(<ClientEmailSenderCard />)

    expect(await screen.findByLabelText("Replies go to")).toHaveValue("frontdesk@example.com")
    expect(preview()).toHaveTextContent(
      "Clients see: Example Therapy <portal@example.com> · replies go to frontdesk@example.com",
    )
  })

  it("while no domain can send, a typed reply-to says it switches over once verified", async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue(sender())
    renderWithProviders(<ClientEmailSenderCard />)

    await user.type(await screen.findByLabelText("Replies go to"), "frontdesk@example.com")

    expect(preview()).toHaveTextContent(
      "Clients see: Example Therapy <mail@deploy.example.net> · replies go to frontdesk@example.com",
    )
    expect(note()).toHaveTextContent(
      "Once your domain's email is verified, this switches to portal@your domain automatically.",
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
