// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the chart shows of the files a form collected.
 *
 * The API modules are mocked rather than the hooks, so the query keys, the
 * `enabled` guards and the no-retry posture are exercised on the way
 * through. Four things this has to get right, and each one is a decision
 * rather than a detail:
 *
 * * an image is fetched as bytes and drawn from an object URL, which is
 *   revoked when the card goes away;
 * * anything that is not an image is a chip, not a broken picture;
 * * the scan badge is dark until something has actually scanned;
 * * the member id is shown by its last four and never in full.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"

import { IntakeArtifacts, fileSize, maskMemberId } from "../IntakeArtifacts"
import type { IntakeArtifactGroup } from "@/hooks/useIntakeArtifacts"
import type { IntakeChartArtifact } from "@/lib/api/intakeReview"
import { renderWithProviders } from "@/test/renderWithProviders"

const mockSignedUrl = vi.fn()
const mockCoverage = vi.fn()

vi.mock("@/lib/api/patientDocuments", () => ({
  getPatientDocumentDownloadUrl: (...args: unknown[]) => mockSignedUrl(...args),
}))

vi.mock("@/lib/api/coverage", () => ({
  fetchCoverage: (...args: unknown[]) => mockCoverage(...args),
}))

const SIGNED = "https://storage.example/signed/card-front.png"
const OBJECT_URL = "blob:pablo/card-front"

function artifact(overrides: Partial<IntakeChartArtifact> = {}): IntakeChartArtifact {
  return {
    id: "artifact-1",
    item_id: "item-card",
    item_label: "A photo of your insurance card",
    side: "front",
    document_id: "doc-1",
    filename: "card-front.png",
    content_type: "image/png",
    size_bytes: 2048,
    scan_status: null,
    created_at: "2026-03-14T12:00:00Z",
    ...overrides,
  }
}

function group(artifacts: IntakeChartArtifact[]): IntakeArtifactGroup[] {
  return [
    {
      assignment: {
        id: "assignment-1",
        version_id: "version-1",
        packet_name: "Before we meet",
        version: 2,
        status: "submitted",
        assigned_at: "2026-03-01T12:00:00Z",
        submitted_at: "2026-03-14T12:00:00Z",
        receipt_code: "K7M2QP4T",
        progress: { complete: true, missing: [] },
      },
      artifacts,
    },
  ]
}

function render(artifacts: IntakeChartArtifact[]) {
  return renderWithProviders(
    <IntakeArtifacts patientId="patient-a" groups={group(artifacts)} />,
  )
}

describe("IntakeArtifacts", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCoverage.mockResolvedValue(null)
    mockSignedUrl.mockResolvedValue(SIGNED)
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["bytes"]) }),
    )
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue(OBJECT_URL),
      revokeObjectURL: vi.fn(),
    })
  })

  it("renders nothing when no form collected anything", () => {
    renderWithProviders(<IntakeArtifacts patientId="patient-a" groups={[]} />)
    expect(screen.queryByTestId("intake-artifacts")).not.toBeInTheDocument()
  })

  it("draws an image from an object URL over bytes it fetched itself", async () => {
    render([artifact()])

    const image = await screen.findByTestId("intake-artifact-thumbnail")
    expect(image).toHaveAttribute("src", OBJECT_URL)
    expect(image).toHaveAttribute("alt", "card-front.png")
    // Through the authenticated route, which is what records the read.
    expect(mockSignedUrl).toHaveBeenCalledWith("doc-1", undefined, "inline")
    expect(globalThis.fetch).toHaveBeenCalledWith(SIGNED)
  })

  it("revokes the object URL when the card goes away", async () => {
    const { unmount } = render([artifact()])

    await screen.findByTestId("intake-artifact-thumbnail")
    unmount()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith(OBJECT_URL)
  })

  it("says so rather than showing a broken picture when the bytes do not arrive", async () => {
    mockSignedUrl.mockRejectedValue(new Error("gone"))

    render([artifact()])

    expect(await screen.findByTestId("intake-artifact-thumbnail-failed")).toBeInTheDocument()
    expect(screen.queryByTestId("intake-artifact-thumbnail")).not.toBeInTheDocument()
  })

  it("shows a chip for a file that is not an image", async () => {
    render([
      artifact({
        content_type: "application/pdf",
        filename: "referral.pdf",
        side: null,
      }),
    ])

    expect(await screen.findByTestId("intake-artifact-chip")).toBeInTheDocument()
    expect(screen.queryByTestId("intake-artifact-thumbnail")).not.toBeInTheDocument()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it("labels which side of the card a photograph is", async () => {
    render([artifact({ side: "back", filename: "card-back.png" })])
    expect(await screen.findByText(/Back · card-back\.png/)).toBeInTheDocument()
  })

  it("names the question the file answers and how big it is", async () => {
    render([artifact()])
    expect(await screen.findByText("A photo of your insurance card")).toBeInTheDocument()
    expect(screen.getByText("2.0 KB")).toBeInTheDocument()
  })

  it("shows no scan badge while nothing has scanned", async () => {
    render([artifact()])
    await screen.findByTestId("intake-artifact")
    expect(screen.queryByTestId("intake-artifact-scan")).not.toBeInTheDocument()
  })

  it("shows what the scanner did once one has looked", async () => {
    render([artifact({ scan_status: "clean" })])
    expect(await screen.findByTestId("intake-artifact-scan")).toHaveTextContent("Scanned")
  })

  it("shows an unrecognised scan state as it stands rather than guessing", async () => {
    render([artifact({ scan_status: "quarantined" })])
    expect(await screen.findByTestId("intake-artifact-scan")).toHaveTextContent("quarantined")
  })
})

describe("IntakeArtifacts coverage summary", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSignedUrl.mockResolvedValue(SIGNED)
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["bytes"]) }),
    )
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue(OBJECT_URL),
      revokeObjectURL: vi.fn(),
    })
  })

  it("shows the payer, the last four of the member id, and the verdict", async () => {
    mockCoverage.mockResolvedValue({
      id: "coverage-1",
      patient_id: "patient-a",
      payer: { id: "payer-1", name: "Blue Shield of California", payer_id: "BS001" },
      member_id: "XQZ998877123456",
      group_number: null,
      plan_name: null,
      active: true,
      copay_override_cents: null,
      verified_at: "2026-03-14T12:00:00Z",
      eligibility: {
        status: "active",
        checked_at: "2026-03-14T12:00:00Z",
        payer_name: "Blue Shield of California",
        plan_name: null,
        plan_begin: null,
        copay_cents: null,
        coinsurance_pct: null,
        deductible_remaining_cents: null,
        visit_limit: null,
        requires_authorization: null,
        carveout_administrator: null,
        aaa_errors: [],
      },
      created_at: "2026-03-01T12:00:00Z",
      updated_at: "2026-03-14T12:00:00Z",
    })

    render([artifact()])

    expect(await screen.findByTestId("intake-coverage")).toBeInTheDocument()
    expect(screen.getByText("Blue Shield of California")).toBeInTheDocument()
    expect(screen.getByText("Member ID ending 3456")).toBeInTheDocument()
    // The number on the card is never on the screen in full.
    expect(screen.queryByText(/XQZ998877123456/)).not.toBeInTheDocument()
    expect(screen.getByTestId("eligibility-badge")).toHaveTextContent(
      "Plan active as of",
    )
  })

  it("shows nothing when no plan is on file", async () => {
    mockCoverage.mockResolvedValue(null)

    render([artifact()])

    await screen.findByTestId("intake-artifact")
    await waitFor(() => expect(mockCoverage).toHaveBeenCalled())
    expect(screen.queryByTestId("intake-coverage")).not.toBeInTheDocument()
  })
})

describe("how a file is described", () => {
  it.each([
    [1, "1 bytes"],
    [900, "900 bytes"],
    [2048, "2.0 KB"],
    [5_242_880, "5.0 MB"],
  ])("writes %i bytes as %s", (size, written) => {
    expect(fileSize(size)).toBe(written)
  })

  it("keeps only the last four of a member id", () => {
    expect(maskMemberId("XQZ998877123456")).toBe("3456")
  })
})
