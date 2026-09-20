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
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IntakeItemEditor } from "../IntakeItemEditor"
import {
  HELP_TEXT_FIELD,
  LABEL_FIELD,
  LABEL_FIELD_OVERRIDE,
  LABEL_PLACEHOLDER,
  NO_QUESTIONS,
  PUBLISHED_NOTICE,
} from "../intakeCopy"
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

function item(key: string, item_type: string, config = {}, label: string | null = null) {
  return {
    id: `item-${key}`,
    key,
    position: 0,
    item_type: item_type as IntakeVersionDetail["items"][number]["item_type"],
    required: true,
    resign_on_new_version: false,
    label,
    help_text: null,
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

  it("puts the refusal beside the question the server named", () => {
    editor(version({ items: [item("reason", "reason"), item("how_bad", "scale")] }), {
      publishError: "how_bad: the top of the scale is not above the bottom",
    })

    // Once, next to that question, with the key it already carries on the
    // row stripped off the message — and not again at the foot of the form.
    const alerts = screen.getAllByRole("alert")
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toHaveTextContent("the top of the scale is not above the bottom")
    expect(alerts[0].textContent).not.toContain("how_bad:")
  })

  it("offers a question the ones above it to be shown because of", async () => {
    const user = userEvent.setup()
    editor(
      version({
        items: [
          item("drinks", "single_choice", {
            options: [
              { key: "yes", label: "Yes" },
              { key: "no", label: "No" },
            ],
          }, "Do you drink?"),
          item("how_often", "free_text", {}, "How often?"),
        ],
      })
    )

    await user.click(screen.getByRole("button", { name: /How often\?/ }))
    await user.click(screen.getByRole("combobox", { name: "Based on" }))

    expect(screen.getByRole("option", { name: "Do you drink?" })).toBeInTheDocument()
    // Not itself, and nothing below it: a rule may only look backwards.
    expect(screen.queryByRole("option", { name: "How often?" })).not.toBeInTheDocument()
  })

  it("saves the rule the picker wrote", async () => {
    const user = userEvent.setup()
    editor(
      version({
        items: [
          item("smokes", "yes_no", {}, "Do you smoke?"),
          item("how_many", "number", {}, "How many a day?"),
        ],
      })
    )

    await user.click(screen.getByRole("button", { name: /How many a day\?/ }))
    await user.click(screen.getByRole("combobox", { name: "Based on" }))
    await user.click(screen.getByRole("option", { name: "Do you smoke?" }))
    await user.click(screen.getByRole("combobox", { name: "When they" }))
    await user.click(screen.getByRole("option", { name: "said yes" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave.mock.calls[onSave.mock.calls.length - 1][0][1].config).toEqual({
      visible_when: { item_key: "smokes", op: "eq", value: true },
    })
  })

  it("takes a rule back off again", async () => {
    const user = userEvent.setup()
    editor(
      version({
        items: [
          item("smokes", "yes_no", {}, "Do you smoke?"),
          item(
            "how_many",
            "number",
            { visible_when: { item_key: "smokes", op: "eq", value: true } },
            "How many a day?"
          ),
        ],
      })
    )

    await user.click(screen.getByRole("button", { name: /How many a day\?/ }))
    await user.click(screen.getByRole("combobox", { name: "Based on" }))
    await user.click(screen.getByRole("option", { name: "Always ask this" }))
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave.mock.calls[onSave.mock.calls.length - 1][0][1].config).toEqual({})
  })

  it("a published version is read-only and says what to do instead", () => {
    editor(
      version({ published_at: "2026-09-02T09:00:00Z", items: [item("reason", "reason")] })
    )

    expect(screen.getByText(PUBLISHED_NOTICE)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add question" })).not.toBeInTheDocument()
  })

  it("saves the question and the help text a practice writes", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("mood", "free_text")] }))

    await user.click(screen.getByRole("button", { name: /Written answer/ }))
    await user.type(screen.getByLabelText(LABEL_FIELD), "H")
    await user.type(screen.getByLabelText(HELP_TEXT_FIELD), "A")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave).toHaveBeenCalledWith([
      expect.objectContaining({ key: "mood", label: "H", help_text: "A" }),
    ])
  })

  it("says what goes in the box rather than that it is required", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("mood", "free_text")] }))

    await user.click(screen.getByRole("button", { name: /Written answer/ }))

    expect(screen.getByLabelText(LABEL_FIELD)).toHaveAttribute("placeholder", LABEL_PLACEHOLDER)
  })

  it("offers a heading override on a question Pablo already words", async () => {
    const user = userEvent.setup()
    editor(version({ items: [item("reason", "reason")] }))

    await user.click(screen.getByRole("button", { name: /What brings you in/ }))

    expect(screen.getByLabelText(LABEL_FIELD_OVERRIDE)).toBeInTheDocument()
    expect(screen.queryByLabelText(LABEL_FIELD)).not.toBeInTheDocument()
  })

  it.each([
    ["section", "Section heading"],
    ["instructions", "Instructions"],
  ])("leaves %s alone, because its text is already its own setting", async (itemType, heading) => {
    const user = userEvent.setup()
    editor(version({ items: [item("about", itemType)] }))

    await user.click(screen.getByRole("button", { name: new RegExp(heading) }))

    expect(screen.queryByLabelText(LABEL_FIELD)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(LABEL_FIELD_OVERRIDE)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(HELP_TEXT_FIELD)).not.toBeInTheDocument()
  })

  it("lists a question by its own words once they are written", () => {
    editor(
      version({
        items: [item("mood", "free_text", {}, "How have you been sleeping?")],
      }),
    )

    const list = screen.getByRole("list")
    expect(within(list).getByText("How have you been sleeping?")).toBeInTheDocument()
    expect(within(list).queryByText("Written answer")).not.toBeInTheDocument()
  })

  it("falls back to the kind of question until they are", () => {
    editor(version({ items: [item("mood", "free_text")] }))

    expect(within(screen.getByRole("list")).getByText("Written answer")).toBeInTheDocument()
  })

  it("lists a published version's questions by their own words too", () => {
    editor(
      version({
        published_at: "2026-09-02T09:00:00Z",
        items: [item("mood", "free_text", {}, "How have you been sleeping?")],
      }),
    )

    expect(screen.getByText("How have you been sleeping?")).toBeInTheDocument()
  })

  it("offers every kind of question, consent documents included", async () => {
    const user = userEvent.setup()
    editor(version())

    await user.click(screen.getByRole("combobox", { name: "Kind of question" }))

    expect(screen.getByRole("option", { name: "Consent to sign" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Measure" })).toBeInTheDocument()
  })
})
