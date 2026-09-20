// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Documents card in Settings > Patient portal.
 *
 * What matters here is the difference between a draft and a published
 * version, because everything else on the screen follows from it. A draft
 * gets an editor and a Publish button; a published version gets the words
 * it froze, a line saying what to do instead, and a way to start the next
 * one. The server refuses the edit either way — this is so a practice does
 * not type into a box whose contents can never be saved.
 *
 * The preview is asserted to be the server's `rendered_html` rather than
 * anything rendered here, which is the property that keeps what a practice
 * proofreads the same as what a patient is shown.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IntakeDocumentsCard } from "../IntakeDocumentsCard"
import {
  DOCUMENTS_EMPTY,
  DOCUMENT_PUBLISHED_NOTICE,
  DRAFT_BADGE,
  NEW_DOCUMENT_NAME,
  PUBLISHED_BADGE,
} from "../intakeCopy"
import type { IntakeDocument } from "@/types/intakeDocuments"

const mockUseDocuments = vi.fn()
const mockCreate = vi.fn()
const mockSave = vi.fn()
const mockPublish = vi.fn()
const mockNewVersion = vi.fn()

/** What the publish mutation last failed with, so a test can set it. */
let publishError: Error | null = null

vi.mock("@/hooks/useIntakeDocuments", () => ({
  useIntakeDocuments: () => mockUseDocuments(),
  useCreateIntakeDocument: () => ({ mutate: mockCreate, isPending: false, error: null }),
  useSaveIntakeDocument: () => ({ mutate: mockSave, isPending: false, error: null }),
  usePublishIntakeDocument: () => ({ mutate: mockPublish, isPending: false, error: publishError }),
  useNewIntakeDocumentVersion: () => ({ mutate: mockNewVersion, isPending: false, error: null }),
}))

const DRAFT: IntakeDocument = {
  id: "document-2",
  document_key: "key-1",
  title: "Consent for treatment",
  body_markdown: "# Consent\n\nPlease read this.",
  rendered_html: "<h2>Consent</h2>\n<p>Please read this.</p>",
  version: 2,
  digest: "a".repeat(64),
  published_at: null,
  requires_signature: true,
  signer_roles: ["patient"],
  created_at: "2026-09-02T10:00:00Z",
}

const PUBLISHED: IntakeDocument = {
  ...DRAFT,
  id: "document-1",
  version: 1,
  rendered_html: "<h2>Consent</h2>\n<p>The words that went live.</p>",
  published_at: "2026-09-01T12:00:00Z",
}

describe("IntakeDocumentsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    publishError = null
    mockUseDocuments.mockReturnValue({ data: [DRAFT] })
  })

  it("says so when the practice has no documents", () => {
    mockUseDocuments.mockReturnValue({ data: [] })

    render(<IntakeDocumentsCard />)

    expect(screen.getByText(DOCUMENTS_EMPTY)).toBeInTheDocument()
  })

  it("lists each document with its version and state", () => {
    render(<IntakeDocumentsCard />)

    expect(screen.getByText("Consent for treatment")).toBeInTheDocument()
    expect(screen.getByText("Version 2")).toBeInTheDocument()
    expect(screen.getByText(DRAFT_BADGE)).toBeInTheDocument()
  })

  it("shows the published state when the newest version is frozen", () => {
    mockUseDocuments.mockReturnValue({ data: [PUBLISHED] })

    render(<IntakeDocumentsCard />)

    expect(screen.getByText(PUBLISHED_BADGE)).toBeInTheDocument()
  })

  it("a draft opens into an editor holding its own text", async () => {
    const user = userEvent.setup()
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))

    expect(screen.getByLabelText("Name")).toHaveValue("Consent for treatment")
    expect(screen.getByLabelText("What they read")).toHaveValue(DRAFT.body_markdown)
  })

  it("the preview is the rendering the server sent", async () => {
    const user = userEvent.setup()
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))

    expect(screen.getByLabelText("Preview")).toContainHTML(DRAFT.rendered_html)
  })

  it("saving sends the title and the text together", async () => {
    const user = userEvent.setup()
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))
    await user.type(screen.getByLabelText("Name"), "!")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockSave).toHaveBeenCalledWith({
      id: "document-2",
      input: {
        title: "Consent for treatment!",
        body_markdown: DRAFT.body_markdown,
      },
    })
  })

  it("publishing names the open document", async () => {
    const user = userEvent.setup()
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))
    await user.click(screen.getByRole("button", { name: "Publish" }))

    expect(mockPublish).toHaveBeenCalledWith("document-2")
  })

  it("a published version offers a new one instead of an editor", async () => {
    const user = userEvent.setup()
    mockUseDocuments.mockReturnValue({ data: [PUBLISHED] })
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))

    expect(screen.getByText(DOCUMENT_PUBLISHED_NOTICE)).toBeInTheDocument()
    expect(screen.queryByLabelText("What they read")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Start a new version" }))
    expect(mockNewVersion).toHaveBeenCalledWith("document-1", expect.anything())
  })

  it("a published version still shows the words it froze", async () => {
    const user = userEvent.setup()
    mockUseDocuments.mockReturnValue({ data: [PUBLISHED] })
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))

    expect(screen.getByText("The words that went live.")).toBeInTheDocument()
  })

  it("adding a document asks for one", async () => {
    const user = userEvent.setup()
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: "Add a document" }))

    expect(mockCreate).toHaveBeenCalledWith(
      { title: NEW_DOCUMENT_NAME, body_markdown: "" },
      expect.anything()
    )
  })

  it("what the server said about a refused publish is shown as it said it", async () => {
    const user = userEvent.setup()
    publishError = new Error("This version is already published.")
    render(<IntakeDocumentsCard />)

    await user.click(screen.getByRole("button", { name: /Consent for treatment/ }))

    expect(screen.getByRole("alert")).toHaveTextContent("This version is already published.")
  })
})
