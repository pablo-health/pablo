// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SourceParamsEditor tests (PABLO-6x5.9).
 *
 * Both editors must emit a backend-valid SourceParams shape so a visible
 * source never sends the boolean-vs-shape mismatch that errored the turn.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"

import { SourceParamsEditor } from "../SourceParamsEditor"

const listDocs = vi.fn()
vi.mock("@/hooks/usePatientDocuments", () => ({
  usePatientDocuments: () => listDocs(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  listDocs.mockReturnValue({
    data: {
      data: [
        { id: "doc-1", filename: "intake.pdf" },
        { id: "doc-2", filename: "labs.pdf" },
      ],
      total: 2,
    },
    isLoading: false,
  })
})

describe("SourceParamsEditor — pasted_text", () => {
  it("applies a {content} shape, never a bare true", () => {
    const onApply = vi.fn() as Mock
    render(
      <SourceParamsEditor
        sourceKey="pasted_text"
        patientId="p1"
        value={undefined}
        onApply={onApply}
      />,
    )

    fireEvent.change(screen.getByTestId("pasted-text-input"), {
      target: { value: "external note text" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))

    expect(onApply).toHaveBeenCalledWith({ content: "external note text" })
  })
})

describe("SourceParamsEditor — patient_documents", () => {
  it("defaults to all documents (true)", () => {
    const onApply = vi.fn() as Mock
    render(
      <SourceParamsEditor
        sourceKey="patient_documents"
        patientId="p1"
        value={undefined}
        onApply={onApply}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(onApply).toHaveBeenCalledWith(true)
  })

  it("applies a picked subset as {document_ids}", () => {
    const onApply = vi.fn() as Mock
    render(
      <SourceParamsEditor
        sourceKey="patient_documents"
        patientId="p1"
        value={undefined}
        onApply={onApply}
      />,
    )

    fireEvent.click(screen.getByLabelText(/select specific documents/i))
    const picker = screen.getByTestId("patient-documents-picker")
    fireEvent.click(within(picker).getByText("labs.pdf"))

    fireEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(onApply).toHaveBeenCalledWith({ document_ids: ["doc-2"] })
  })

  it("seeds specific mode from an existing document_ids value", () => {
    const onApply = vi.fn() as Mock
    render(
      <SourceParamsEditor
        sourceKey="patient_documents"
        patientId="p1"
        value={{ document_ids: ["doc-1"] }}
        onApply={onApply}
      />,
    )

    // Already in specific mode with doc-1 checked; apply round-trips it.
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))
    expect(onApply).toHaveBeenCalledWith({ document_ids: ["doc-1"] })
  })
})
