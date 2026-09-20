// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One editor per kind of question.
 *
 * Each case asserts two things: the fields that kind needs are on screen, and
 * editing one writes the key the server expects. The second half is what keeps
 * a rename here from quietly producing a configuration the publish endpoint
 * rejects, which a snapshot of the markup would not notice.
 *
 * The four fixed kinds render nothing, and that is asserted too — an empty
 * panel under "Name and date of birth" is the correct answer, not a gap.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ItemConfigForm } from "../ItemConfigForm"
import type { ItemConfig, ItemType } from "@/types/intakePackets"
import { DOCUMENT_PICKER_LABEL, NO_PUBLISHED_DOCUMENTS } from "../intakeCopy"

const onChange = vi.fn()

function form(itemType: ItemType, config: ItemConfig = {}) {
  return render(
    <ItemConfigForm
      itemType={itemType}
      config={config}
      onChange={onChange}
      idPrefix="test"
    />
  )
}

/** The config the last onChange was called with. */
function lastConfig(): ItemConfig {
  return onChange.mock.calls[onChange.mock.calls.length - 1][0]
}

describe("ItemConfigForm", () => {
  beforeEach(() => vi.clearAllMocks())

  it("a section heading takes a title", async () => {
    const user = userEvent.setup()
    form("section")

    await user.type(screen.getByLabelText("Heading"), "A")

    expect(lastConfig()).toEqual({ title: "A" })
  })

  it("instructions take a body", async () => {
    const user = userEvent.setup()
    form("instructions")

    await user.type(screen.getByLabelText("What they read"), "H")

    expect(lastConfig()).toEqual({ body_markdown: "H" })
  })

  it("a written answer takes a length limit", async () => {
    const user = userEvent.setup()
    form("free_text")

    await user.type(screen.getByLabelText("Longest answer, in characters"), "5")

    expect(lastConfig()).toEqual({ max_len: 5 })
  })

  it("a pick-one question builds its list of answers", async () => {
    const user = userEvent.setup()
    form("single_choice")

    await user.click(screen.getByRole("button", { name: "Add an answer" }))

    expect(lastConfig()).toEqual({ options: [{ key: "answer_1", label: "Answer 1" }] })
  })

  it("relabelling an answer keeps its stored name", async () => {
    const user = userEvent.setup()
    form("single_choice", { options: [{ key: "never", label: "Never" }] })

    // The component is controlled and `onChange` is a spy, so the value it
    // renders never advances — one keystroke appends to the original label.
    // What is being asserted is the key beside it, which does not move.
    await user.type(screen.getByLabelText("Answer 1"), "R")

    expect(lastConfig().options).toEqual([{ key: "never", label: "NeverR" }])
  })

  it("an answer can be removed", async () => {
    const user = userEvent.setup()
    form("single_choice", {
      options: [
        { key: "a", label: "A" },
        { key: "b", label: "B" },
      ],
    })

    await user.click(screen.getByRole("button", { name: "Remove answer 1" }))

    expect(lastConfig().options).toEqual([{ key: "b", label: "B" }])
  })

  it("a pick-any question also bounds how many they may pick", async () => {
    const user = userEvent.setup()
    form("multi_choice")

    await user.type(screen.getByLabelText("Fewest they may pick"), "1")

    expect(lastConfig()).toEqual({ min: 1 })
    expect(screen.getByLabelText("Most they may pick")).toBeInTheDocument()
  })

  it("a yes-or-no question may ask for more", async () => {
    const user = userEvent.setup()
    form("yes_no")

    await user.type(screen.getByLabelText("If yes, also ask"), "W")

    expect(lastConfig()).toEqual({ follow_up_label: "W" })
  })

  it("a scale takes both ends and a word for each", async () => {
    const user = userEvent.setup()
    form("scale", { min: 0, max: 10 })

    await user.type(screen.getByLabelText("Word at the bottom"), "N")

    expect(lastConfig()).toEqual({ min: 0, max: 10, min_label: "N" })
    expect(screen.getByLabelText("Word at the top")).toBeInTheDocument()
    expect(screen.getByLabelText("Top of the scale")).toHaveValue(10)
  })

  it("a number takes a range and a unit", async () => {
    const user = userEvent.setup()
    form("number")

    await user.type(screen.getByLabelText("Unit"), "m")

    expect(lastConfig()).toEqual({ unit: "m" })
    expect(screen.getByLabelText("Smallest")).toBeInTheDocument()
    expect(screen.getByLabelText("Largest")).toBeInTheDocument()
  })

  it("a date takes an earliest and a latest", () => {
    form("date", { min: "2000-01-01" })

    expect(screen.getByLabelText("Earliest")).toHaveValue("2000-01-01")
    expect(screen.getByLabelText("Latest")).toBeInTheDocument()
  })

  it("a measure offers only the ones a patient can fill in", async () => {
    const user = userEvent.setup()
    form("instrument")

    await user.click(screen.getByRole("combobox", { name: "Which measure" }))

    expect(screen.getByRole("option", { name: "PHQ-9" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "GAD-7" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /DIRE/i })).not.toBeInTheDocument()
  })

  it.each(["demographics", "reason", "emergency_contact", "guardian"] as ItemType[])(
    "%s has nothing for a practice to set",
    (itemType) => {
      // The engine fixes the shape of these four, so there is nothing here
      // to configure. What to ASK for is the item's own question, which
      // lives beside the name rather than in here — see IntakeItemEditor.
      const { container } = form(itemType)
      expect(container).toBeEmptyDOMElement()
    },
  )

  it("an insurance card asks how many photos, and whether to type the plan", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <ItemConfigForm
        itemType="insurance_card"
        config={{}}
        onChange={onChange}
        idPrefix="test"
      />,
    )

    // Both sides is the default, because a plan is printed across the two.
    expect(screen.getByRole("combobox", { name: "How many photos" })).toHaveTextContent(
      "Front and back",
    )

    await user.click(screen.getByLabelText(/type the plan details/i))

    expect(onChange).toHaveBeenCalledWith({ collect_fields: true })
  })

  it("and narrows to one photo when a practice only needs the front", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <ItemConfigForm
        itemType="insurance_card"
        config={{}}
        onChange={onChange}
        idPrefix="test"
      />,
    )

    await user.click(screen.getByRole("combobox", { name: "How many photos" }))
    await user.click(screen.getByRole("option", { name: "Front only" }))

    expect(onChange).toHaveBeenCalledWith({ sides: "front" })
  })

  it("a document request says to upload a blank form before it can offer one", () => {
    render(
      <ItemConfigForm
        itemType="document_request"
        config={{}}
        onChange={vi.fn()}
        idPrefix="test"
      />,
    )

    expect(
      screen.getByText("Upload a blank form first, then you can offer it here."),
    ).toBeInTheDocument()
  })

  it("and offers the practice's own forms when it has some", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <ItemConfigForm
        itemType="document_request"
        config={{}}
        onChange={onChange}
        idPrefix="test"
        blankForms={[{ id: "form-1", title: "Release of records" }]}
      />,
    )

    await user.click(screen.getByRole("combobox", { name: /Offer a form to download/i }))
    await user.click(screen.getByRole("option", { name: "Release of records" }))

    expect(onChange).toHaveBeenCalledWith({ blank_form_id: "form-1" })
  })

  it("and lets a practice take the offer back off", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <ItemConfigForm
        itemType="document_request"
        config={{ blank_form_id: "form-1" }}
        onChange={onChange}
        idPrefix="test"
        blankForms={[{ id: "form-1", title: "Release of records" }]}
      />,
    )

    await user.click(screen.getByRole("combobox", { name: /Offer a form to download/i }))
    await user.click(screen.getByRole("option", { name: "Don't offer one" }))

    // Absent rather than empty: an empty string is not a form id, and the
    // server's model would refuse it.
    expect(onChange).toHaveBeenCalledWith({ blank_form_id: undefined })
  })
})

describe("ItemConfigForm: the consent document picker", () => {
  beforeEach(() => vi.clearAllMocks())

  const DOCUMENTS = [
    { document_key: "key-1", title: "Consent for treatment" },
    { document_key: "key-2", title: "Telehealth agreement" },
  ]

  function consentForm(config: ItemConfig = {}, documents = DOCUMENTS) {
    return render(
      <ItemConfigForm
        itemType="consent_document"
        config={config}
        onChange={onChange}
        idPrefix="test"
        documents={documents}
      />
    )
  }

  it("offers each published document by name", async () => {
    const user = userEvent.setup()
    consentForm()

    await user.click(screen.getByLabelText(DOCUMENT_PICKER_LABEL))

    expect(screen.getByRole("option", { name: "Consent for treatment" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Telehealth agreement" })).toBeInTheDocument()
  })

  it("picking one stores the document, not a version of it", async () => {
    const user = userEvent.setup()
    consentForm()

    await user.click(screen.getByLabelText(DOCUMENT_PICKER_LABEL))
    await user.click(screen.getByRole("option", { name: "Telehealth agreement" }))

    expect(lastConfig()).toEqual({ document_key: "key-2" })
  })

  it("with nothing published it says what to do first", () => {
    consentForm({}, [])

    expect(screen.getByText(NO_PUBLISHED_DOCUMENTS)).toBeInTheDocument()
    expect(screen.queryByLabelText(DOCUMENT_PICKER_LABEL)).not.toBeInTheDocument()
  })
})
