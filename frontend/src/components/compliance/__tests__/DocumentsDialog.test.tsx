// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { DocumentsDialog } from "../DocumentsDialog"
import type { ComplianceDocument, ComplianceItem } from "@/types/compliance"

const uploadMutate = vi.hoisted(() => vi.fn())
const deleteMutate = vi.hoisted(() => vi.fn())
const documentsState = vi.hoisted(() => ({
  data: [] as ComplianceDocument[],
  isLoading: false,
}))

vi.mock("@/hooks/useCompliance", () => ({
  useComplianceDocuments: () => documentsState,
  useUploadComplianceDocument: () => ({
    mutate: uploadMutate,
    isPending: false,
  }),
  useDeleteComplianceDocument: () => ({
    mutate: deleteMutate,
    isPending: false,
    variables: undefined,
  }),
}))

const ITEM: ComplianceItem = {
  id: "item-1",
  item_type: "maps_registration",
  label: "MAPS (AWARxE) registration",
  due_date: null,
  notes: null,
  completed_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

function makeDoc(overrides: Partial<ComplianceDocument> = {}): ComplianceDocument {
  return {
    id: "doc-1",
    compliance_item_id: "item-1",
    filename: "maps-confirmation.pdf",
    mime_type: "application/pdf",
    size_bytes: 2048,
    document_type: "maps_registration",
    description: null,
    uploaded_at: "2026-06-01T00:00:00Z",
    uploaded_by_user_id: "user-1",
    ...overrides,
  }
}

function pickFile(name: string, type: string, size: number) {
  const file = new File(["x"], name, { type })
  Object.defineProperty(file, "size", { value: size })
  const input = screen.getByLabelText(
    "Choose a document to upload",
  ) as HTMLInputElement
  fireEvent.change(input, { target: { files: [file] } })
}

function renderDialog() {
  return render(
    <DocumentsDialog open onOpenChange={() => {}} item={ITEM} />,
  )
}

beforeEach(() => {
  uploadMutate.mockReset()
  deleteMutate.mockReset()
  documentsState.data = []
  documentsState.isLoading = false
})

describe("DocumentsDialog", () => {
  it("shows the empty state when no documents are attached", () => {
    renderDialog()
    expect(screen.getByText(/No documents yet/i)).toBeInTheDocument()
  })

  it("lists attached documents with their size", () => {
    documentsState.data = [makeDoc()]
    renderDialog()
    expect(screen.getByText("maps-confirmation.pdf")).toBeInTheDocument()
    expect(screen.getByText("2 KB")).toBeInTheDocument()
  })

  it("uploads a valid file with the item's type as document_type", () => {
    renderDialog()
    pickFile("cert.pdf", "application/pdf", 1024)
    expect(uploadMutate).toHaveBeenCalledTimes(1)
    expect(uploadMutate.mock.calls[0][0]).toMatchObject({
      documentType: "maps_registration",
    })
  })

  it("rejects an unsupported file type without uploading", () => {
    renderDialog()
    pickFile("notes.txt", "text/plain", 1024)
    expect(uploadMutate).not.toHaveBeenCalled()
    expect(screen.getByRole("alert")).toHaveTextContent(/Unsupported file type/i)
  })

  it("rejects a file over the size limit without uploading", () => {
    renderDialog()
    pickFile("huge.pdf", "application/pdf", 26 * 1024 * 1024)
    expect(uploadMutate).not.toHaveBeenCalled()
    expect(screen.getByRole("alert")).toHaveTextContent(/25 MB limit/i)
  })

  it("removes a document when its delete control is clicked", () => {
    documentsState.data = [makeDoc()]
    renderDialog()
    fireEvent.click(screen.getByLabelText("Remove maps-confirmation.pdf"))
    expect(deleteMutate).toHaveBeenCalledWith("doc-1")
  })
})
