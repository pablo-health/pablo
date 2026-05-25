// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * DocumentViewerSheet tests (PABLO-6x5.3)
 *
 * The signed-URL fetch is mocked (it fires the access audit server-side,
 * covered by the backend suite); here we assert the viewer renders the
 * right element per mime type, requests an `inline` URL, and surfaces
 * loading / error states.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

import { DocumentViewerSheet } from "../DocumentViewerSheet"
import type { PatientDocumentResponse } from "@/types/patientDocuments"

const getUrl = vi.fn()
vi.mock("@/lib/api/patientDocuments", () => ({
  getPatientDocumentDownloadUrl: (...args: unknown[]) => getUrl(...args),
}))

function makeDoc(
  overrides: Partial<PatientDocumentResponse> = {},
): PatientDocumentResponse {
  return {
    id: "doc-1",
    patient_id: "patient-1",
    filename: "intake.pdf",
    mime_type: "application/pdf",
    size_bytes: 1234,
    created_at: "2026-05-24T10:00:00Z",
    finalized_at: "2026-05-24T10:00:01Z",
    category: "chart",
    extracted_text: null,
    text_extraction_failed: false,
    ...overrides,
  }
}

describe("DocumentViewerSheet", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getUrl.mockResolvedValue("https://fake.googleusercontent.example/signed")
  })

  it("renders nothing when closed", () => {
    render(
      <DocumentViewerSheet document={makeDoc()} open={false} onOpenChange={() => {}} />,
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(getUrl).not.toHaveBeenCalled()
  })

  it("fetches an inline-disposition URL and renders a PDF embed", async () => {
    render(
      <DocumentViewerSheet document={makeDoc()} open onOpenChange={() => {}} />,
    )

    await waitFor(() => {
      expect(getUrl).toHaveBeenCalledWith("doc-1", undefined, "inline")
    })

    const dialog = await screen.findByRole("dialog")
    await waitFor(() => {
      const embed = dialog.querySelector('embed[type="application/pdf"]')
      expect(embed).toHaveAttribute(
        "src",
        "https://fake.googleusercontent.example/signed",
      )
    })
  })

  it("renders an img for image documents", async () => {
    render(
      <DocumentViewerSheet
        document={makeDoc({ filename: "scan.png", mime_type: "image/png" })}
        open
        onOpenChange={() => {}}
      />,
    )

    const img = await screen.findByRole("img", { name: "scan.png" })
    expect(img).toHaveAttribute(
      "src",
      "https://fake.googleusercontent.example/signed",
    )
  })

  it("surfaces an error when the signed-URL fetch fails", async () => {
    getUrl.mockRejectedValue(new Error("403 forbidden"))
    render(
      <DocumentViewerSheet document={makeDoc()} open onOpenChange={() => {}} />,
    )

    expect(await screen.findByText("403 forbidden")).toBeInTheDocument()
  })
})
