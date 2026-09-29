// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SendFormsFlow tests — choose, review, send.
 *
 * Starting an intake is two routes and one action, so most of what matters
 * is the order they run in and what the clinician is told when only one of
 * them worked. The API modules are mocked rather than the hooks, so the
 * query keys and invalidation are exercised on the way through.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { SendFormsFlow } from "../SendFormsFlow"
import { sendableForms } from "../sendable"
import { SendIntakeForm } from "../../SendIntakeForm"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { IntakeTemplate } from "@/types/intakePackets"

const mockTemplates = vi.fn()
const mockAssign = vi.fn()
const mockAccess = vi.fn()
const mockInvite = vi.fn()
const mockPatient = vi.fn()
const mockWording = vi.fn()
const mockPreview = vi.fn()

vi.mock("@/lib/api/intakePackets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/intakePackets")>()
  return { ...actual, listIntakeTemplates: (...a: unknown[]) => mockTemplates(...a) }
})

vi.mock("@/lib/api/intakeReview", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/intakeReview")>()
  return { ...actual, assignIntakePacket: (...a: unknown[]) => mockAssign(...a) }
})

vi.mock("@/lib/api/portalAccess", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/portalAccess")>()
  return {
    ...actual,
    getPortalAccess: (...a: unknown[]) => mockAccess(...a),
    issuePortalInvite: (...a: unknown[]) => mockInvite(...a),
  }
})

// The practice's portal parts. Every part on unless a test says otherwise.
const mockPortalSettings = vi.fn()
vi.mock("@/lib/api/portalSettings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/portalSettings")>()),
  getPortalSettings: (...a: unknown[]) => mockPortalSettings(...a),
}))

vi.mock("@/lib/api/patients", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patients")>()
  return { ...actual, getPatient: (...a: unknown[]) => mockPatient(...a) }
})

vi.mock("@/lib/api/inviteTemplate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/inviteTemplate")>()
  return {
    ...actual,
    getInviteTemplate: (...a: unknown[]) => mockWording(...a),
    previewClientInvite: (...a: unknown[]) => mockPreview(...a),
  }
})

function version(id: string, n: number, published: string | null) {
  return { id, version: n, published_at: published, created_at: "2026-03-01T12:00:00Z" }
}

function template(overrides: Partial<IntakeTemplate> = {}): IntakeTemplate {
  return {
    id: "template-1",
    name: "Before we meet",
    created_at: "2026-03-01T12:00:00Z",
    archived_at: null,
    versions: [version("version-1", 1, "2026-03-02T12:00:00Z")],
    ...overrides,
  }
}

const SECOND = template({
  id: "template-2",
  name: "Consent to treatment",
  versions: [version("version-2", 1, "2026-03-02T12:00:00Z")],
})

const NO_ACCESS = {
  patient_id: "patient-a",
  invite_outstanding: false,
  live_sessions: 0,
  portal_enabled: true,
  revoked_at: null,
}

const onDone = vi.fn()

/** Inside an open dialog, as both of its hosts render it. */
function renderFlow(props: { chartHref?: string } = {}) {
  return renderWithProviders(
    <Dialog open>
      <DialogContent>
        <SendFormsFlow
          patientId="patient-a"
          onDone={onDone}
          header={
            <DialogHeader>
              <DialogTitle>Send forms</DialogTitle>
            </DialogHeader>
          }
          {...props}
        />
      </DialogContent>
    </Dialog>,
  )
}

async function tick(name: string) {
  await userEvent.click(await screen.findByRole("checkbox", { name }))
}

async function review() {
  await userEvent.click(screen.getByRole("button", { name: "Review" }))
}

async function send() {
  await userEvent.click(await screen.findByTestId("send-forms-send"))
}

describe("sendableForms", () => {
  it("offers only the newest published version of each form", () => {
    const forms = sendableForms([
      template({
        versions: [
          version("v1", 1, "2026-01-01T12:00:00Z"),
          version("v3", 3, "2026-03-01T12:00:00Z"),
          version("v4", 4, null),
        ],
      }),
    ])
    expect(forms).toHaveLength(1)
    expect(forms[0].version.id).toBe("v3")
  })

  it("drops a form with nothing published and a form that is archived", () => {
    const forms = sendableForms([
      template({ id: "a", versions: [version("draft", 1, null)] }),
      template({ id: "b", archived_at: "2026-04-01T12:00:00Z" }),
    ])
    expect(forms).toEqual([])
  })
})

describe("SendFormsFlow", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPortalSettings.mockResolvedValue({
      enabled: true,
      decided: true,
      modules: { intake: true, messaging: true },
    })
    mockTemplates.mockResolvedValue([template(), SECOND])
    mockAssign.mockResolvedValue({ id: "assignment-1" })
    mockAccess.mockResolvedValue(NO_ACCESS)
    mockInvite.mockResolvedValue({ patient_id: "patient-a", invite_expires_at: 1 })
    mockPatient.mockResolvedValue({
      id: "patient-a",
      first_name: "Robin",
      last_name: "Reyes",
      email: "robin@example.test",
      phone: "+15005550006",
    })
    mockWording.mockResolvedValue({ editable: true, subject: "s", body: "b", is_default: true, placeholders: [] })
    mockPreview.mockResolvedValue({
      available: true,
      to_email: "robin@example.test",
      subject: "Your sign-in link",
      text: "Hi Robin,\n- Before we meet\n[personal sign-in link]",
    })
  })

  it("reviews exactly what was chosen, then sends the forms before the invitation", async () => {
    renderFlow()
    await tick("Before we meet")
    await review()

    expect(screen.getByTestId("send-forms-review-list")).toHaveTextContent("Before we meet")
    expect(screen.getByTestId("send-forms-review-list")).not.toHaveTextContent("Consent")
    expect(screen.getByTestId("send-forms-review-portal")).toHaveTextContent(
      "A sign-in link goes to robin@example.test, and a code by text to +15005550006.",
    )

    await send()
    await waitFor(() => expect(mockInvite).toHaveBeenCalled())
    expect(mockAssign).toHaveBeenCalledWith("patient-a", "version-1", undefined)
    expect(mockAssign.mock.invocationCallOrder[0]).toBeLessThan(
      mockInvite.mock.invocationCallOrder[0],
    )
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent(
      "Forms and invitation sent",
    )
    expect(screen.getByTestId("send-forms-outcome")).toHaveTextContent(
      "They'll get a link by email at robin@example.test and a code by text at +15005550006.",
    )
    expect(screen.getByTestId("send-forms-sent-list")).toHaveTextContent("Before we meet")
    // The question the dialog opened with is gone, not left above the answer.
    expect(screen.queryByText("Send forms")).not.toBeInTheDocument()
  })

  it("previews the invitation email for this client before anything is sent", async () => {
    renderFlow()
    await tick("Before we meet")
    await review()
    await userEvent.click(screen.getByRole("button", { name: "Preview email" }))

    expect(await screen.findByTestId("invite-email-preview-text")).toHaveTextContent("Hi Robin,")
    expect(mockPreview).toHaveBeenCalledWith("patient-a", ["version-1"])
    expect(mockAssign).not.toHaveBeenCalled()
    expect(mockInvite).not.toHaveBeenCalled()
  })

  it("offers no email preview where the wording is fixed", async () => {
    mockWording.mockResolvedValue({ editable: false, subject: "", body: "", is_default: true, placeholders: [] })
    renderFlow()
    await tick("Before we meet")
    await review()
    await waitFor(() => expect(mockWording).toHaveBeenCalled())
    expect(screen.queryByRole("button", { name: "Preview email" })).not.toBeInTheDocument()
  })

  it("sends the forms alone when the invitation is unticked", async () => {
    renderFlow()
    await tick("Before we meet")
    await tick("Invite them to the portal")
    await review()
    expect(screen.getByTestId("send-forms-review-portal")).toHaveTextContent("No invitation.")
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalled())
    expect(mockInvite).not.toHaveBeenCalled()
  })

  it("can invite without sending any form", async () => {
    renderFlow()
    await screen.findByRole("checkbox", { name: "Invite them to the portal" })
    await review()
    await send()
    await waitFor(() => expect(mockInvite).toHaveBeenCalled())
    expect(mockAssign).not.toHaveBeenCalled()
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent("Invitation sent")
    expect(screen.queryByTestId("send-forms-sent-list")).not.toBeInTheDocument()
  })

  it("never says a client can sign in to a portal that is off, even with a live session", async () => {
    mockAccess.mockResolvedValue({ ...NO_ACCESS, live_sessions: 1, portal_enabled: false })
    renderFlow()
    await tick("Before we meet")
    await review()
    expect(screen.getByTestId("send-forms-review-portal")).not.toHaveTextContent(
      "They can already sign in",
    )
    await send()

    // Forms went; the client cannot open them, and the screen says so.
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent("Forms sent")
    expect(screen.getByTestId("send-forms-outcome")).toHaveTextContent(
      "They'll need an invitation to the portal to open them.",
    )
    expect(screen.getByTestId("send-forms-outcome")).not.toHaveTextContent("waiting in their portal")
  })

  it("offers no forms, and says why, where the practice turned Forms off", async () => {
    mockPortalSettings.mockResolvedValue({
      enabled: true,
      decided: true,
      modules: { intake: false, messaging: true },
    })
    renderFlow()

    expect(await screen.findByTestId("send-forms-forms-off")).toHaveTextContent(
      "Forms are turned off in your client portal.",
    )
    expect(screen.queryByRole("checkbox", { name: "Before we meet" })).not.toBeInTheDocument()
  })

  it("points at the setting instead of offering an invitation when the practice's portal is off", async () => {
    mockAccess.mockResolvedValue({ ...NO_ACCESS, portal_enabled: false })
    renderFlow()
    const note = await screen.findByTestId("send-forms-portal-off")
    expect(note).toHaveTextContent("To invite them to the portal, turn on the client portal.")
    expect(screen.getByRole("link", { name: "turn on the client portal" })).toHaveAttribute(
      "href",
      "/dashboard/settings/portal",
    )
    expect(screen.queryByRole("checkbox", { name: "Invite them to the portal" })).not.toBeInTheDocument()
  })

  it("does not offer an invitation to a client who can already sign in", async () => {
    mockAccess.mockResolvedValue({ ...NO_ACCESS, live_sessions: 1 })
    renderFlow()
    expect(await screen.findByText("They can already sign in to the portal.")).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Invite them to the portal" })).not.toBeInTheDocument()
    await tick("Before we meet")
    await review()
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalled())
    expect(mockInvite).not.toHaveBeenCalled()
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent("Forms sent")
    expect(screen.getByTestId("send-forms-outcome")).toHaveTextContent(
      "They're waiting in their portal.",
    )
  })

  it("says what is missing when the client has no mobile number", async () => {
    mockPatient.mockResolvedValue({ id: "patient-a", first_name: "Robin", last_name: "Reyes", email: "robin@example.test", phone: null })
    renderFlow()
    expect(await screen.findByTestId("send-forms-contact-missing")).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Invite them to the portal" })).not.toBeInTheDocument()
  })

  it("sends forms alone on a deployment with no portal", async () => {
    mockAccess.mockRejectedValue(Object.assign(new Error("no portal"), { status: 404 }))
    renderFlow()
    await tick("Before we meet")
    await review()
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalled())
    expect(mockInvite).not.toHaveBeenCalled()
    // No portal here, so nothing is missing: no call for an invitation.
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent("Forms sent")
    expect(screen.queryByTestId("send-forms-outcome")).not.toBeInTheDocument()
  })

  it("points at setup when the practice has published no form", async () => {
    mockTemplates.mockResolvedValue([template({ versions: [version("draft", 1, null)] })])
    renderFlow()
    const none = await screen.findByTestId("send-forms-none-published")
    expect(none).toHaveTextContent("No forms are published yet.")
    expect(screen.getByRole("link", { name: "Set up forms" })).toHaveAttribute(
      "href",
      "/dashboard/settings/portal",
    )
  })

  it("names what went out when a later form fails, and sends no invitation", async () => {
    mockAssign
      .mockResolvedValueOnce({ id: "assignment-1" })
      .mockRejectedValueOnce(Object.assign(new Error("boom"), { status: 500 }))
    renderFlow()
    await tick("Before we meet")
    await tick("Consent to treatment")
    await review()
    await send()
    expect(await screen.findByTestId("send-forms-error")).toHaveTextContent(
      "Before we meet went out; the rest did not.",
    )
    expect(mockInvite).not.toHaveBeenCalled()
  })

  it("says the forms are out when the invitation needs contact details", async () => {
    mockInvite.mockRejectedValue(Object.assign(new Error("unprocessable"), { status: 422 }))
    renderFlow()
    await tick("Before we meet")
    await review()
    await send()
    // The heading names the failure; it never claims an invitation went.
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent(
      "Forms sent. The invitation didn't go out.",
    )
    expect(screen.getByTestId("send-forms-outcome")).toHaveTextContent(
      /email address and a mobile number on file/i,
    )
  })

  it("names a failed invitation in the heading when nothing else was sent", async () => {
    mockInvite.mockRejectedValue(Object.assign(new Error("boom"), { status: 500 }))
    renderFlow()
    await review()
    await send()
    expect(await screen.findByTestId("send-forms-heading")).toHaveTextContent("Invitation not sent")
    expect(screen.getByTestId("send-forms-outcome")).toHaveTextContent(
      "You can try again from the chart.",
    )
  })

  it("offers the chart from the acknowledgment when it was opened for a new client", async () => {
    renderFlow({ chartHref: "/dashboard/patients/patient-a" })
    await tick("Before we meet")
    await review()
    await send()
    expect(await screen.findByRole("link", { name: "Open client’s chart" })).toHaveAttribute(
      "href",
      "/dashboard/patients/patient-a",
    )
    await userEvent.click(screen.getByRole("button", { name: "Done" }))
    expect(onDone).toHaveBeenCalled()
  })

  it("leaves without sending anything", async () => {
    renderFlow()
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }))
    expect(onDone).toHaveBeenCalled()
    expect(mockAssign).not.toHaveBeenCalled()
    expect(mockInvite).not.toHaveBeenCalled()
  })
})

describe("SendIntakeForm", () => {
  it("opens the same flow from the chart", async () => {
    mockTemplates.mockResolvedValue([template()])
    mockAccess.mockResolvedValue(NO_ACCESS)
    mockPatient.mockResolvedValue({ id: "patient-a", first_name: "Robin", last_name: "Reyes", email: null, phone: null })
    mockWording.mockResolvedValue({ editable: true, subject: "s", body: "b", is_default: true, placeholders: [] })
    renderWithProviders(<SendIntakeForm patientId="patient-a" />)
    await userEvent.click(screen.getByTestId("send-intake-form-button"))
    expect(await screen.findByTestId("send-forms-choose")).toBeInTheDocument()
  })
})
