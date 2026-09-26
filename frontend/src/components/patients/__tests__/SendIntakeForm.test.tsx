// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SendIntakeForm tests.
 *
 * Starting an intake is two routes and one action, so most of what matters
 * here is what the clinician is told when only one of them worked. The API
 * modules are mocked rather than the hooks, so the query keys and the
 * invalidation that refreshes the chart's list are exercised on the way
 * through.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { SendIntakeForm, sendableForms } from "../SendIntakeForm"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { IntakeTemplate } from "@/types/intakePackets"

const mockTemplates = vi.fn()
const mockAssign = vi.fn()
const mockAccess = vi.fn()
const mockInvite = vi.fn()

vi.mock("@/lib/api/intakePackets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/intakePackets")>()
  return { ...actual, listIntakeTemplates: (...a: unknown[]) => mockTemplates(...a) }
})

vi.mock("@/lib/api/intakeReview", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/intakeReview")>()
  return {
    ...actual,
    assignIntakePacket: (...a: unknown[]) => mockAssign(...a),
    listIntakeAssignments: vi.fn().mockResolvedValue([]),
    listIntakeArtifacts: vi.fn().mockResolvedValue([]),
  }
})

vi.mock("@/lib/api/portalAccess", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/portalAccess")>()
  return {
    ...actual,
    getPortalAccess: (...a: unknown[]) => mockAccess(...a),
    issuePortalInvite: (...a: unknown[]) => mockInvite(...a),
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

/** No way in yet: nothing outstanding, nothing live. */
const NO_ACCESS = {
  patient_id: "patient-a",
  invite_outstanding: false,
  live_sessions: 0,
  revoked_at: null,
}

function render() {
  return renderWithProviders(<SendIntakeForm patientId="patient-a" />)
}

async function send() {
  await userEvent.click(await screen.findByTestId("send-intake-form-button"))
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

describe("SendIntakeForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockTemplates.mockResolvedValue([template()])
    mockAssign.mockResolvedValue({ id: "assignment-1" })
    mockAccess.mockResolvedValue(NO_ACCESS)
    mockInvite.mockResolvedValue({ patient_id: "patient-a", invite_expires_at: 1 })
  })

  it("renders nothing when the practice has published no form", async () => {
    mockTemplates.mockResolvedValue([template({ versions: [version("draft", 1, null)] })])
    render()
    await waitFor(() => expect(mockTemplates).toHaveBeenCalled())
    expect(screen.queryByTestId("send-intake-form")).not.toBeInTheDocument()
  })

  it("sends the published version and mints an invite when there is no way in", async () => {
    render()
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalledWith("patient-a", "version-1", undefined))
    expect(mockInvite).toHaveBeenCalled()
    expect(await screen.findByTestId("send-intake-form-sent")).toHaveTextContent(
      /link by email and a code by text/i,
    )
  })

  it("does not mint a second invite for a patient who can already get in", async () => {
    mockAccess.mockResolvedValue({ ...NO_ACCESS, live_sessions: 1 })
    render()
    await waitFor(() => expect(mockAccess).toHaveBeenCalled())
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalled())
    expect(mockInvite).not.toHaveBeenCalled()
    expect(await screen.findByTestId("send-intake-form-sent")).toHaveTextContent(
      /waiting in their portal/i,
    )
  })

  it("sends the form alone when the deployment runs no portal", async () => {
    mockAccess.mockRejectedValue(Object.assign(new Error("no portal"), { status: 404 }))
    render()
    await waitFor(() => expect(mockAccess).toHaveBeenCalled())
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalled())
    expect(mockInvite).not.toHaveBeenCalled()
  })

  it("says the form is out when the patient has no email or mobile on file", async () => {
    mockInvite.mockRejectedValue(Object.assign(new Error("unprocessable"), { status: 422 }))
    render()
    await send()
    await waitFor(() => expect(mockAssign).toHaveBeenCalled())
    expect(await screen.findByTestId("send-intake-form-sent")).toHaveTextContent(
      /email address and a mobile number on file/i,
    )
  })

  it("names the cause when the version stopped being published", async () => {
    mockAssign.mockRejectedValue(Object.assign(new Error("unprocessable"), { status: 422 }))
    render()
    await send()
    expect(await screen.findByTestId("send-intake-form-error")).toHaveTextContent(
      /no longer published/i,
    )
    expect(mockInvite).not.toHaveBeenCalled()
  })
})
