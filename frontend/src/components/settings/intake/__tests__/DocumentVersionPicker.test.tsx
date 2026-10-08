// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which published wording a consent question asks for. Newest by default;
 * an earlier published version can be chosen, and choosing "newest" again
 * clears the choice so publishing takes whatever is newest then.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { DocumentVersionPicker } from "../DocumentVersionPicker"

const mockVersions = vi.fn()
vi.mock("@/hooks/useIntakeDocuments", () => ({
  usePublishedVersions: (key: string) => mockVersions(key),
}))

const V3 = { id: "rev-3", version: 3, published_at: "2026-09-30T12:00:00Z" }
const V2 = { id: "rev-2", version: 2, published_at: "2026-08-01T12:00:00Z" }

describe("DocumentVersionPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockVersions.mockReturnValue({ data: [V3, V2] })
  })

  it("defaults to the newest and can choose an earlier published wording", async () => {
    const user = userEvent.setup()
    const onChoose = vi.fn()
    render(<DocumentVersionPicker documentKey="consent" chosen={undefined} onChoose={onChoose} idPrefix="t" />)

    expect(mockVersions).toHaveBeenCalledWith("consent")
    const picker = screen.getByRole("combobox", { name: "Which wording" })
    expect(picker).toHaveTextContent("The newest when you publish (now version 3)")

    await user.click(picker)
    await user.click(screen.getByRole("option", { name: /^Version 2, published/ }))
    expect(onChoose).toHaveBeenCalledWith("rev-2")
  })

  it("going back to the newest clears the choice", async () => {
    const user = userEvent.setup()
    const onChoose = vi.fn()
    render(<DocumentVersionPicker documentKey="consent" chosen="rev-2" onChoose={onChoose} idPrefix="t" />)

    await user.click(screen.getByRole("combobox", { name: "Which wording" }))
    await user.click(screen.getByRole("option", { name: /^The newest when you publish/ }))
    expect(onChoose).toHaveBeenCalledWith(undefined)
  })

  it("offers nothing when there is only one published wording", () => {
    mockVersions.mockReturnValue({ data: [V3] })
    render(<DocumentVersionPicker documentKey="consent" chosen={undefined} onChoose={vi.fn()} idPrefix="t" />)
    expect(screen.queryByRole("combobox", { name: "Which wording" })).not.toBeInTheDocument()
  })
})
