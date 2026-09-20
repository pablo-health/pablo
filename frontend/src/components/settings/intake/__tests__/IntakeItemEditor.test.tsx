// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The question editor for one draft version.
 *
 * What matters: the list can be built, reordered and saved in the order it is
 * shown; each kind of question gets its own settings; a published version is
 * read-only with a line saying what to do instead; and the server's refusal is
 * shown as the server worded it, because that string names the question that
 * is wrong.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IntakeItemEditor } from "../IntakeItemEditor"
import { NO_QUESTIONS, PUBLISHED_NOTICE } from "../intakeCopy"
import type { IntakeVersionDetail } from "@/types/intakePackets"

const onSave = vi.fn()
const onPublish = vi.fn()

function version(overrides: Partial<IntakeVersionDetail> = {}): IntakeVersionDetail {
  return {
    id: "version-1",
    template_id: "template-1",
    version: 1,
    published_at: null,
    created_at: "2026-09-01T10:00:00Z",
    items: [],
    ...overrides,
  }
}

function item(key: string, item_type: string, config = {}) {
  return {
    id: `item-${key}`,
    key,
    position: 0,
    item_type: item_type as IntakeVersionDetail["items"][number]["item_type"],
    required: true,
    resign_on_new_version: false,
    config,
  }
}

function editor(v: IntakeVersionDetail, props = {}) {
  return render(
    <IntakeItemEditor version={v} onSave={onSave} onPublish={onPublish} {...props} />
  )
}

describe("IntakeItemEditor", () => {
  beforeEach(() => vi.clearAllMocks())

  it("says so when the form has nothing on it yet", () => {
    editor(version())
    expect(screen.getByText(NO_QUESTIONS)).toBeInTheDocument()
  })

  it("adds a question of the chosen kind", async () => {
    const user = userEvent.setup()
    editor(version())

    await user.click(screen.getByRole("button", { name: "Add question" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave).toHaveBeenCalledWith([
      expect.objectContaining({ item_type: "free_text", key: "written_answer" }),
    ])
  })

  it("names a second question of the same kind without colliding", async () => {
    const user = userEvent.setup()
    editor(version())

    await user.click(screen.getByRole("button", { name: "Add question" }))
    await user.click(screen.getByRole("button", { name: "Add question" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave.mock.calls[0][0].map((i: { key: string }) => i.key)).toEqual([
      "written_answer",
      "written_answer_2",
    ])
  })

  it("saves the list in the order it is shown", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("reason", "reason"), item("mood", "free_text")] }))

    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave).toHaveBeenCalledWith([
      expect.objectContaining({ key: "reason" }),
      expect.objectContaining({ key: "mood" }),
    ])
  })

  it("reorders a question and saves the new order", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("reason", "reason"), item("mood", "free_text")] }))

    await user.click(screen.getByRole("button", { name: "Move mood up" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave.mock.calls[0][0].map((i: { key: string }) => i.key)).toEqual([
      "mood",
      "reason",
    ])
  })

  it("removes a question", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("reason", "reason"), item("mood", "free_text")] }))

    await user.click(screen.getByRole("button", { name: "Remove mood" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave.mock.calls[0][0].map((i: { key: string }) => i.key)).toEqual(["reason"])
  })

  it("does not offer a required toggle on a heading", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("about", "section", { title: "About you" })] }))

    await user.click(screen.getByRole("button", { expanded: false }))

    expect(screen.queryByText("They have to answer this")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Heading")).toHaveValue("About you")
  })

  it("offers a required toggle on a question and saves it off", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("mood", "free_text")] }))

    await user.click(screen.getByRole("button", { expanded: false }))
    await user.click(screen.getByRole("switch", { name: "mood has to be answered" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave.mock.calls[0][0][0].required).toBe(false)
  })

  it("publishes on request", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("reason", "reason")] }))

    await user.click(screen.getByRole("button", { name: "Publish" }))

    expect(onPublish).toHaveBeenCalled()
  })

  it("shows the server's refusal as the server worded it", () => {
    editor(version({ items: [item("reason", "reason")] }), {
      publishError: "how_bad: the top of the scale is not above the bottom",
    })

    expect(screen.getByRole("alert")).toHaveTextContent(
      "how_bad: the top of the scale is not above the bottom"
    )
  })

  it("a published version is read-only and says what to do instead", () => {
    editor(
      version({ published_at: "2026-09-02T09:00:00Z", items: [item("reason", "reason")] })
    )

    expect(screen.getByText(PUBLISHED_NOTICE)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add question" })).not.toBeInTheDocument()
  })

  it("does not offer a consent document, which cannot be stored yet", async () => {
    const user = userEvent.setup()
    editor(version())

    await user.click(screen.getByRole("combobox", { name: "Kind of question" }))

    expect(screen.queryByRole("option", { name: "Consent to sign" })).not.toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Measure" })).toBeInTheDocument()
  })
})
