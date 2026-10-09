// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Writing a document from the consent question that asks for it: created and
 * published through the same calls the Documents card makes, then handed
 * back by key so the question points at it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NewDocumentInline } from "../NewDocumentInline"

const mockCreate = vi.fn()
const mockPublish = vi.fn()

vi.mock("@/hooks/useIntakeDocuments", () => ({
  useCreateIntakeDocument: () => ({ mutateAsync: mockCreate, isPending: false, error: null }),
  usePublishIntakeDocument: () => ({ mutateAsync: mockPublish, isPending: false, error: null }),
}))

describe("NewDocumentInline", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCreate.mockResolvedValue({ id: "doc-1", document_key: "privacy", title: "Privacy notice" })
    mockPublish.mockResolvedValue({ id: "doc-1", document_key: "privacy", title: "Privacy notice" })
  })

  it("creates, publishes, then hands back the key and title", async () => {
    const user = userEvent.setup()
    const onCreated = vi.fn()
    render(<NewDocumentInline idPrefix="t" onCreated={onCreated} />)

    await user.type(screen.getByLabelText("Document name"), "Privacy notice")
    await user.type(screen.getByLabelText("What they read"), "How we keep your records.")
    await user.click(screen.getByRole("button", { name: "Publish and use it" }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("privacy", "Privacy notice"))
    expect(mockCreate).toHaveBeenCalledWith({
      title: "Privacy notice",
      body_markdown: "How we keep your records.",
    })
    expect(mockPublish).toHaveBeenCalledWith("doc-1")
  })

  it("says what's missing instead of greying out Publish", async () => {
    const user = userEvent.setup()
    const onCreated = vi.fn()
    render(<NewDocumentInline idPrefix="t" onCreated={onCreated} />)

    const publish = screen.getByRole("button", { name: "Publish and use it" })
    expect(publish).toBeEnabled()
    await user.click(publish)

    expect(mockCreate).not.toHaveBeenCalled()
    expect(screen.getByLabelText("Document name")).toHaveFocus()
    expect(screen.getByText("Give the document a name.")).toBeInTheDocument()
    expect(screen.getByText("Write what they read.")).toBeInTheDocument()
  })
})
