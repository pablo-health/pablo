// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The Forms card in Settings > Patient portal.
 *
 * What matters here is the wiring the editor cannot see: which form is open,
 * which of its versions, and that publishing and saving name that version.
 * The questions themselves are the editor's tests.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IntakeFormsCard } from "../IntakeFormsCard"
import { DRAFT_BADGE, EMPTY_STATE, PUBLISHED_BADGE } from "../intakeCopy"
import type { IntakeTemplate, IntakeVersionDetail } from "@/types/intakePackets"

const mockUseTemplates = vi.fn()
const mockUseVersion = vi.fn()
const mockCreateTemplate = vi.fn()
const mockCreateVersion = vi.fn()
const mockSaveItems = vi.fn()
const mockPublish = vi.fn()

// The card asks which documents a consent question could point at. The
// picker itself is the item editor's test; here it only has to not be a
// network call.
const mockUsePublishedDocuments = vi.fn()

vi.mock("@/hooks/useIntakeDocuments", () => ({
  usePublishedIntakeDocuments: () => mockUsePublishedDocuments(),
}))

// And which blank forms a document question could offer. Same arrangement
// and same reason as the documents above.
const mockUseBlankForms = vi.fn()

vi.mock("@/hooks/useIntakeBlankForms", () => ({
  useIntakeBlankForms: () => mockUseBlankForms(),
}))

vi.mock("@/hooks/useIntakePackets", () => ({
  useIntakeTemplates: () => mockUseTemplates(),
  useIntakeVersion: (...args: unknown[]) => mockUseVersion(...args),
  useCreateIntakeTemplate: () => ({ mutate: mockCreateTemplate, isPending: false, error: null }),
  useCreateIntakeVersion: () => ({ mutate: mockCreateVersion, isPending: false, error: null }),
  useSaveIntakeItems: () => ({ mutate: mockSaveItems, isPending: false, error: null }),
  usePublishIntakeVersion: () => ({ mutate: mockPublish, isPending: false, error: null }),
}))

const DRAFT: IntakeVersionDetail = {
  id: "version-2",
  template_id: "template-1",
  version: 2,
  published_at: null,
  created_at: "2026-09-02T10:00:00Z",
  items: [
    {
      id: "item-1",
      key: "reason",
      position: 0,
      item_type: "reason",
      required: true,
      resign_on_new_version: false,
      label: null,
      help_text: null,
      config: {},
    },
  ],
}

const TEMPLATE: IntakeTemplate = {
  id: "template-1",
  name: "Intake",
  created_at: "2026-09-01T10:00:00Z",
  archived_at: null,
  versions: [
    { id: "version-2", version: 2, published_at: null, created_at: "2026-09-02T10:00:00Z" },
    {
      id: "version-1",
      version: 1,
      published_at: "2026-09-01T12:00:00Z",
      created_at: "2026-09-01T10:00:00Z",
    },
  ],
}

describe("IntakeFormsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseTemplates.mockReturnValue({ data: [TEMPLATE] })
    mockUseVersion.mockReturnValue({ data: DRAFT })
    mockUsePublishedDocuments.mockReturnValue({ data: [] })
    mockUseBlankForms.mockReturnValue({ data: [] })
  })

  it("says so when the practice has no forms", () => {
    mockUseTemplates.mockReturnValue({ data: [] })

    render(<IntakeFormsCard />)

    expect(screen.getByText(EMPTY_STATE)).toBeInTheDocument()
  })

  it("lists each form with the state of its newest version", () => {
    render(<IntakeFormsCard />)

    expect(screen.getByText("Intake")).toBeInTheDocument()
    expect(screen.getByText(DRAFT_BADGE)).toBeInTheDocument()
  })

  it("shows the published state when the newest version is frozen", () => {
    mockUseTemplates.mockReturnValue({
      data: [{ ...TEMPLATE, versions: [TEMPLATE.versions[1]] }],
    })

    render(<IntakeFormsCard />)

    expect(screen.getByText(PUBLISHED_BADGE)).toBeInTheDocument()
  })

  it("opens the newest version when a form is opened", async () => {
    const user = userEvent.setup()
    render(<IntakeFormsCard />)

    await user.click(screen.getByRole("button", { name: "Intake" }))

    expect(mockUseVersion).toHaveBeenCalledWith("template-1", "version-2")
    expect(screen.getByRole("button", { name: "Version 1" })).toBeInTheDocument()
  })

  it("saving names the open version", async () => {
    const user = userEvent.setup()
    render(<IntakeFormsCard />)

    await user.click(screen.getByRole("button", { name: "Intake" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mockSaveItems).toHaveBeenCalledWith(
      expect.objectContaining({ templateId: "template-1", versionId: "version-2" })
    )
  })

  it("publishing names the open version", async () => {
    const user = userEvent.setup()
    render(<IntakeFormsCard />)

    await user.click(screen.getByRole("button", { name: "Intake" }))
    await user.click(screen.getByRole("button", { name: "Publish" }))

    expect(mockPublish).toHaveBeenCalledWith({
      templateId: "template-1",
      versionId: "version-2",
    })
  })

  it("a published version offers a new one instead of an editor", async () => {
    const user = userEvent.setup()
    mockUseVersion.mockReturnValue({
      data: { ...DRAFT, id: "version-1", version: 1, published_at: "2026-09-01T12:00:00Z" },
    })
    render(<IntakeFormsCard />)

    await user.click(screen.getByRole("button", { name: "Intake" }))

    await user.click(screen.getByRole("button", { name: "Start a new version" }))
    expect(mockCreateVersion).toHaveBeenCalledWith("template-1", expect.anything())
  })

  it("adding a form asks for one", async () => {
    const user = userEvent.setup()
    render(<IntakeFormsCard />)

    await user.click(screen.getByRole("button", { name: "Add a form" }))

    expect(mockCreateTemplate).toHaveBeenCalledWith("New form")
  })
})
