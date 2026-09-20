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
import type { Instrument } from "@/types/instruments"
import type { ItemConfig, ItemType } from "@/types/intakePackets"
import {
  DOCUMENT_PICKER_LABEL,
  MEASURE_NEEDS_PERMISSION,
  NO_PUBLISHED_DOCUMENTS,
} from "../intakeCopy"

const onChange = vi.fn()

/**
 * The catalogue the measure picker is built from.
 *
 * The server answers which measures are askable and which are waiting on a
 * permission, so the fixture is the answer rather than a list of codes: a
 * public-domain one, a restricted one the practice has not licensed, a
 * restricted one it has, and one the engine will never carry the questions
 * for.
 */
const INSTRUMENTS: Instrument[] = [
  {
    code: "phq9",
    display_name: "PHQ-9",
    rights: "public_domain",
    rights_note: "No permission needed.",
    publisher_url: null,
    item_count: 9,
    can_ask_on_a_form: true,
    attested: false,
    attested_at: null,
    license_reference: null,
  },
  {
    code: "gad7",
    display_name: "GAD-7",
    rights: "attestation_required",
    rights_note: "Free for clinical use.",
    publisher_url: null,
    item_count: 7,
    can_ask_on_a_form: true,
    attested: false,
    attested_at: null,
    license_reference: null,
  },
  {
    code: "epds",
    display_name: "EPDS",
    rights: "attestation_required",
    rights_note: "Free for clinical use.",
    publisher_url: null,
    item_count: 10,
    can_ask_on_a_form: true,
    attested: true,
    attested_at: "2026-09-01T12:00:00Z",
    license_reference: null,
  },
  {
    code: "bdi2",
    display_name: "BDI-II",
    rights: "never_ship",
    rights_note: "Sold by its publisher.",
    publisher_url: null,
    item_count: 21,
    can_ask_on_a_form: false,
    attested: false,
    attested_at: null,
    license_reference: null,
  },
]

function form(
  itemType: ItemType,
  config: ItemConfig = {},
  instruments: Instrument[] = INSTRUMENTS
) {
  return render(
    <ItemConfigForm
      itemType={itemType}
      config={config}
      onChange={onChange}
      idPrefix="test"
      instruments={instruments}
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

  it("a measure the engine does not carry is not on the list at all", async () => {
    const user = userEvent.setup()
    form("instrument")

    await user.click(screen.getByRole("combobox", { name: "Which measure" }))

    expect(screen.queryByRole("option", { name: "BDI-II" })).not.toBeInTheDocument()
  })

  it("a restricted measure is greyed until the practice records permission", async () => {
    const user = userEvent.setup()
    form("instrument")

    await user.click(screen.getByRole("combobox", { name: "Which measure" }))

    expect(screen.getByRole("option", { name: "GAD-7" })).toHaveAttribute(
      "aria-disabled",
      "true"
    )
  })

  it("a restricted measure with permission on file can be picked", async () => {
    const user = userEvent.setup()
    form("instrument")

    await user.click(screen.getByRole("combobox", { name: "Which measure" }))

    expect(screen.getByRole("option", { name: "EPDS" })).not.toHaveAttribute(
      "aria-disabled",
      "true"
    )
  })

  it("says where to record permission when something is waiting on it", () => {
    form("instrument")

    expect(screen.getByText(MEASURE_NEEDS_PERMISSION)).toBeInTheDocument()
  })

  it("says nothing about permission when nothing is waiting on it", () => {
    form("instrument", {}, [
      { ...INSTRUMENTS[0] },
      { ...INSTRUMENTS[2] },
    ])

    expect(screen.queryByText(MEASURE_NEEDS_PERMISSION)).not.toBeInTheDocument()
  })

  it.each([
    "demographics",
    "reason",
    "emergency_contact",
    "guardian",
    "insurance_card",
    "document_request",
  ] as ItemType[])("%s has nothing for a practice to set", (itemType) => {
    // What to ask for on an upload is the item's own question, which lives
    // beside the name rather than in here — see IntakeItemEditor.
    const { container } = form(itemType)
    expect(container).toBeEmptyDOMElement()
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
