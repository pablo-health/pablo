// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ConfirmImportResult, ImportProposal, ProposedSeries } from "@/lib/api/scheduling"
import { CalendarReviewStep } from "../CalendarReviewStep"
import type { NewClientName } from "../NewClientNameFields"

function series(overrides: Partial<ProposedSeries> = {}): ProposedSeries {
  return {
    candidate_key: `key-${Math.random()}`,
    summary: "Jane Miller",
    weekday: 0,
    local_start_time: "09:00",
    duration_minutes: 50,
    cadence: "weekly",
    occurrences_in_window: 8,
    occurrences_ahead: 4,
    first_future_start: "2026-09-07T09:00:00Z",
    last_seen: "2026-08-31T09:00:00Z",
    recurrence_rule: "RRULE:FREQ=WEEKLY",
    status: "active",
    confidence: 0.9,
    preselected: true,
    source_identifier: "series:rec-1",
    match: { patient: null, possible: [], suggested_patient_id: null },
    ...overrides,
  }
}

function proposal(series_: ProposedSeries[]): ImportProposal {
  return {
    series: series_,
    left_alone: 3,
    events_read: 40,
    partial: false,
    lookback_days: 90,
    horizon_days: 90,
    timezone: "UTC",
  }
}

function baseProps() {
  return {
    step: 5,
    checked: {},
    onToggle: vi.fn(),
    clientFor: {} as Record<string, string | null>,
    onChooseClient: vi.fn(),
    notClient: {} as Record<string, boolean>,
    onToggleNotClient: vi.fn(),
    names: {} as Record<string, NewClientName>,
    onChangeName: vi.fn(),
    expanded: false,
    onToggleExpanded: vi.fn(),
    onBack: vi.fn(),
    onReviewAgain: vi.fn(),
    onConfirm: vi.fn(),
    confirming: false,
    error: null,
    result: null as ConfirmImportResult | null,
    onFinish: vi.fn(),
  }
}

describe("CalendarReviewStep", () => {
  it("renders the exact title and lede", () => {
    const list = [series({ candidate_key: "a" }), series({ candidate_key: "b" })]
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    expect(screen.getByText("Step 5 · Review")).toBeInTheDocument()
    expect(screen.getByText("Which of these are clients?")).toBeInTheDocument()
    expect(screen.getByText("Select the recurring events you want to add.")).toBeInTheDocument()
  })

  it("lists every proposed series, in the order the API returned them", () => {
    const list = [
      series({ candidate_key: "a", summary: "Amy" }),
      series({ candidate_key: "b", summary: "Ben" }),
      series({ candidate_key: "c", summary: "Cara" }),
    ]
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    const names = screen.getAllByText(/^(Amy|Ben|Cara)$/).map((n) => n.textContent)
    expect(names).toEqual(["Amy", "Ben", "Cara"])
  })

  it("pre-checks only what the API marked preselected, unchecking a looks_finished series", () => {
    const list = [
      series({ candidate_key: "keep", summary: "Kept", preselected: true }),
      series({
        candidate_key: "stale",
        summary: "Stale",
        preselected: false,
        status: "looks_finished",
      }),
    ]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ keep: true, stale: false }}
      />
    )

    const boxes = screen.getAllByRole("checkbox")
    expect(boxes[0]).toBeChecked()
    expect(boxes[1]).not.toBeChecked()
  })

  it("fires onToggle for the row's own candidate_key, excluding it from what's checked", async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    const list = [series({ candidate_key: "a", summary: "Amy" })]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ a: true }}
        onToggle={onToggle}
      />
    )

    await user.click(screen.getByRole("checkbox"))
    expect(onToggle).toHaveBeenCalledWith("a")
  })

  it("shows five rows and a disclosure for a longer proposal, revealing the rest on click", async () => {
    const user = userEvent.setup()
    const list = Array.from({ length: 8 }, (_, i) =>
      series({ candidate_key: `k${i}`, summary: `Client ${i}` })
    )
    const onToggleExpanded = vi.fn()
    render(
      <CalendarReviewStep {...baseProps()} proposal={proposal(list)} onToggleExpanded={onToggleExpanded} />
    )

    expect(screen.getAllByRole("checkbox")).toHaveLength(5)
    const disclosure = screen.getByRole("button", { name: "Show 3 more" })
    await user.click(disclosure)
    expect(onToggleExpanded).toHaveBeenCalledOnce()
  })

  it("offers to show fewer once expanded", () => {
    const list = Array.from({ length: 8 }, (_, i) =>
      series({ candidate_key: `k${i}`, summary: `Client ${i}` })
    )
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} expanded />)

    expect(screen.getByRole("button", { name: "Show fewer" })).toBeInTheDocument()
  })

  it("does not filter any candidate out of what could be confirmed, even hidden behind the disclosure", () => {
    const list = Array.from({ length: 8 }, (_, i) =>
      series({ candidate_key: `k${i}`, summary: `Client ${i}` })
    )
    const allChecked = Object.fromEntries(list.map((s) => [s.candidate_key, true]))
    render(
      <CalendarReviewStep {...baseProps()} proposal={proposal(list)} checked={allChecked} expanded />
    )

    // Every candidate is rendered (and so reachable/checkable) once expanded.
    expect(screen.getAllByRole("checkbox")).toHaveLength(8)
    expect(screen.getByRole("button", { name: /add 8 clients/i })).toBeInTheDocument()
  })

  it('the primary action reads "Add N clients" and updates live with the checkboxes', () => {
    const list = [series({ candidate_key: "a" }), series({ candidate_key: "b" })]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ a: true, b: false }}
      />
    )

    expect(screen.getByRole("button", { name: "Add 1 client" })).toBeInTheDocument()
  })

  it("disables confirm when nothing is checked — no auto-import shortcut", () => {
    const list = [series({ candidate_key: "a" })]
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} checked={{ a: false }} />)

    expect(screen.getByRole("button", { name: /add 0 clients/i })).toBeDisabled()
  })

  it("fires onConfirm from the primary action", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    const list = [series({ candidate_key: "a" })]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ a: true }}
        onConfirm={onConfirm}
      />
    )

    await user.click(screen.getByRole("button", { name: /add 1 client/i }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it("claims nothing about what is kept", () => {
    const list = [series({ candidate_key: "a" })]
    const { container } = render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    // Pablo keeps the therapist's answers now, so "kept nothing" would be untrue.
    expect(container.textContent ?? "").not.toMatch(/kept nothing/i)
  })

  it("never claims a category the heuristic can't verify", () => {
    const list = [series({ candidate_key: "a" })]
    const { container } = render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    const text = container.textContent ?? ""
    expect(text).not.toMatch(/your clients/i)
    expect(text).not.toMatch(/personal/i)
  })

  describe("which client each series is", () => {
    const jane = { patient_id: "p-1", display_name: "Jane Adams", date_of_birth: "1980-01-02" }
    const otherJane = { patient_id: "p-2", display_name: "Jane Adams", date_of_birth: null }

    function rows() {
      return [
        series({
          candidate_key: "certain",
          summary: "Jane A weekly",
          match: { patient: { ...jane }, possible: [], suggested_patient_id: null },
        }),
        series({
          candidate_key: "possible",
          summary: "Jane Adams",
          match: { patient: null, possible: [jane, otherJane], suggested_patient_id: null },
        }),
        series({ candidate_key: "none", summary: "Robin Tran" }),
      ]
    }

    it("names a certain match, offers a choice for a possible one, and a new client otherwise", () => {
      render(
        <CalendarReviewStep
          {...baseProps()}
          proposal={proposal(rows())}
          clientFor={{ certain: "p-1", possible: null, none: null }}
        />
      )

      expect(screen.getByText("Matches Jane Adams")).toBeInTheDocument()

      const choice = screen.getByRole("combobox", { name: "Which client is Jane Adams?" })
      expect(choice).toHaveValue("new")
      expect(
        Array.from((choice as HTMLSelectElement).options).map((option) => option.text)
      ).toEqual(["Jane Adams, born 1/2/1980", "Jane Adams", "New client"])

      expect(screen.getByText("New client", { selector: "span" })).toBeInTheDocument()
      expect(screen.getAllByRole("combobox")).toHaveLength(1)
    })

    it("reports the client picked for a possible match, and New client as none", async () => {
      const user = userEvent.setup()
      const onChooseClient = vi.fn()
      const onToggle = vi.fn()
      render(
        <CalendarReviewStep
          {...baseProps()}
          onToggle={onToggle}
          onChooseClient={onChooseClient}
          proposal={proposal(rows())}
          clientFor={{ certain: "p-1", possible: "p-2", none: null }}
        />
      )

      const choice = screen.getByRole("combobox", { name: "Which client is Jane Adams?" })
      expect(choice).toHaveValue("p-2")
      await user.selectOptions(choice, "p-1")
      await user.selectOptions(choice, "new")

      expect(onChooseClient.mock.calls).toEqual([
        ["possible", "p-1"],
        ["possible", null],
      ])
      // Choosing a client is not ticking or unticking the row.
      expect(onToggle).not.toHaveBeenCalled()
    })
  })

  describe("a new client's name", () => {
    function fields(key: string) {
      const row = screen.getByTestId(`new-client-name-${key}`)
      return {
        row,
        first: within(row).getByLabelText("First name"),
        last: within(row).getByLabelText("Last name"),
      }
    }

    it("shows name fields only on rows that add a new client", () => {
      const jane = { patient_id: "p-1", display_name: "Jane Adams", date_of_birth: null }
      render(
        <CalendarReviewStep
          {...baseProps()}
          proposal={proposal([
            series({
              candidate_key: "certain",
              summary: "Jane A weekly",
              match: { patient: jane, possible: [], suggested_patient_id: null },
            }),
            series({
              candidate_key: "picked",
              summary: "Jane Adams",
              match: { patient: null, possible: [jane], suggested_patient_id: "p-1" },
            }),
            series({ candidate_key: "new", summary: "Robin Tran" }),
          ])}
          clientFor={{ certain: "p-1", picked: "p-1", new: null }}
        />
      )

      expect(screen.getByTestId("new-client-name-new")).toBeInTheDocument()
      expect(screen.queryByTestId("new-client-name-certain")).not.toBeInTheDocument()
      expect(screen.queryByTestId("new-client-name-picked")).not.toBeInTheDocument()
    })

    it("fills in both parts of a clear full name, bare or after a session word", () => {
      render(
        <CalendarReviewStep
          {...baseProps()}
          proposal={proposal([
            series({
              candidate_key: "bare",
              summary: "Casey Morgan",
              suggested_name: { first_name: "Casey", last_name: "Morgan" },
            }),
            series({
              candidate_key: "session",
              summary: "Session with Casey Morgan",
              suggested_name: { first_name: "Casey", last_name: "Morgan" },
            }),
          ])}
        />
      )

      for (const key of ["bare", "session"]) {
        const { row, first, last } = fields(key)
        expect(first).toHaveValue("Casey")
        expect(last).toHaveValue("Morgan")
        // Both parts given: nothing to refer back to.
        expect(within(row).queryByText(/On the calendar/)).not.toBeInTheDocument()
      }
    })

    it("fills in only the whole part of a name cut short, and shows the title", () => {
      render(
        <CalendarReviewStep
          {...baseProps()}
          proposal={proposal([
            series({
              candidate_key: "last-cut",
              summary: "Jane S.",
              suggested_name: { first_name: "Jane", last_name: "" },
            }),
            series({
              candidate_key: "first-cut",
              summary: "J. Smith",
              suggested_name: { first_name: "", last_name: "Smith" },
            }),
          ])}
        />
      )

      const lastCut = fields("last-cut")
      expect(lastCut.first).toHaveValue("Jane")
      expect(lastCut.last).toHaveValue("")
      expect(within(lastCut.row).getByText("On the calendar: Jane S.")).toBeInTheDocument()

      const firstCut = fields("first-cut")
      expect(firstCut.first).toHaveValue("")
      expect(firstCut.last).toHaveValue("Smith")
      expect(within(firstCut.row).getByText("On the calendar: J. Smith")).toBeInTheDocument()
    })

    it.each([
      ["one word", "Jane"],
      ["initials", "K.M."],
      ["initials without dots", "KM"],
    ])("leaves the fields empty for %s and shows the title", (_shape, summary) => {
      render(
        <CalendarReviewStep
          {...baseProps()}
          proposal={proposal([series({ candidate_key: "row", summary, suggested_name: null })])}
        />
      )

      const { row, first, last } = fields("row")
      expect(first).toHaveValue("")
      expect(last).toHaveValue("")
      expect(within(row).getByText(`On the calendar: ${summary}`)).toBeInTheDocument()
    })

    it("reports what is typed", async () => {
      const user = userEvent.setup()
      const onChangeName = vi.fn()
      render(
        <CalendarReviewStep
          {...baseProps()}
          onChangeName={onChangeName}
          proposal={proposal([series({ candidate_key: "row", summary: "K.M." })])}
        />
      )

      await user.type(fields("row").first, "K")

      expect(onChangeName).toHaveBeenLastCalledWith("row", { first_name: "K", last_name: "" })
    })
  })

  describe("not a client", () => {
    it("marks a row as not a client, and says it will be remembered", async () => {
      const user = userEvent.setup()
      const onToggleNotClient = vi.fn()
      render(
        <CalendarReviewStep
          {...baseProps()}
          onToggleNotClient={onToggleNotClient}
          proposal={proposal([series({ candidate_key: "standup", summary: "Standup" })])}
        />
      )

      await user.click(screen.getByRole("button", { name: "Not a client" }))
      expect(onToggleNotClient).toHaveBeenCalledWith("standup")
    })

    it("shows a marked row as remembered, unticked, with a way back", async () => {
      const user = userEvent.setup()
      const onToggleNotClient = vi.fn()
      render(
        <CalendarReviewStep
          {...baseProps()}
          onToggleNotClient={onToggleNotClient}
          checked={{ standup: true }}
          notClient={{ standup: true }}
          proposal={proposal([series({ candidate_key: "standup", summary: "Standup" })])}
        />
      )

      expect(screen.getByText(/Marked as not a client\./)).toBeInTheDocument()
      const box = screen.getByRole("checkbox", { name: "Standup" })
      expect(box).not.toBeChecked()
      expect(box).toBeDisabled()
      // Nothing to add, but the answer still needs saving.
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled()

      await user.click(screen.getByRole("button", { name: "Undo" }))
      expect(onToggleNotClient).toHaveBeenCalledWith("standup")
    })
  })

  it("after confirming, names what was imported and nothing about access", () => {
    const result: ConfirmImportResult = {
      confirmed: [{ candidate_key: "a", patient_id: "p-1", appointments_created: 4 }],
      patients_created: 1,
      appointments_created: 4,
      skipped: [],
      already_scheduled: [],
    }
    render(<CalendarReviewStep {...baseProps()} proposal={proposal([series()])} result={result} />)

    expect(screen.getByText(/1 client added/i)).toBeInTheDocument()
    expect(screen.getByText("4 upcoming appointments added.")).toBeInTheDocument()
    expect(screen.queryByText(/read access/i)).not.toBeInTheDocument()
  })

  it("names a single appointment in the singular", () => {
    const result: ConfirmImportResult = {
      confirmed: [{ candidate_key: "a", patient_id: "p-1", appointments_created: 1 }],
      patients_created: 1,
      appointments_created: 1,
      skipped: [],
      already_scheduled: [],
    }
    render(<CalendarReviewStep {...baseProps()} proposal={proposal([series()])} result={result} />)

    expect(screen.getByText("1 upcoming appointment added.")).toBeInTheDocument()
  })

  it("reports a skipped series honestly rather than staying silent", () => {
    const result: ConfirmImportResult = {
      confirmed: [],
      patients_created: 1,
      appointments_created: 0,
      skipped: ["a"],
      already_scheduled: [],
    }
    render(<CalendarReviewStep {...baseProps()} proposal={proposal([series()])} result={result} />)

    expect(
      screen.getByText("1 couldn’t be added because those times are already booked.")
    ).toBeInTheDocument()
  })

  it("says, one line each, which series were already on the calendar", () => {
    const result: ConfirmImportResult = {
      confirmed: [],
      patients_created: 0,
      appointments_created: 0,
      skipped: [],
      already_scheduled: ["a"],
    }
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal([
          series({ candidate_key: "a", summary: "Jane Miller" }),
          series({ candidate_key: "b", summary: "Sam Lee" }),
        ])}
        result={result}
      />
    )

    expect(screen.getByText("Jane Miller is already on your calendar.")).toBeInTheDocument()
    expect(screen.queryByText(/Sam Lee/)).not.toBeInTheDocument()
  })

  it("fires onFinish from the post-confirm summary", async () => {
    const user = userEvent.setup()
    const onFinish = vi.fn()
    const result: ConfirmImportResult = {
      confirmed: [],
      patients_created: 1,
      appointments_created: 2,
      skipped: [],
      already_scheduled: [],
    }
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal([series()])}
        result={result}
        onFinish={onFinish}
      />
    )

    await user.click(screen.getByRole("button", { name: "View calendar" }))
    expect(onFinish).toHaveBeenCalledOnce()
  })

  it("shows a colleague's client as seen by them, and can't add it", () => {
    const theirs = series({
      candidate_key: "theirs",
      summary: "Grace Hopper",
      preselected: false,
      match: {
        patient: null,
        possible: [],
        suggested_patient_id: null,
        seen_by: ["Dr. Rivera", "Dr. Okafor"],
      },
    })
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal([theirs])}
        checked={{ theirs: true }}
      />
    )

    expect(
      screen.getByText("Already in your practice, seen by Dr. Rivera and Dr. Okafor.")
    ).toBeInTheDocument()
    expect(
      screen.getByText("Ask Dr. Rivera, Dr. Okafor, or your practice owner for access.")
    ).toBeInTheDocument()
    const box = screen.getByRole("checkbox", { name: "Grace Hopper" })
    expect(box).not.toBeChecked()
    expect(box).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Not a client" })).not.toBeInTheDocument()
    // Nothing to add, so nothing to confirm.
    expect(screen.getByRole("button", { name: "Add 0 clients" })).toBeDisabled()
  })

  it("offers a way back to the scan when jumped to before one", () => {
    render(<CalendarReviewStep {...baseProps()} proposal={null} />)

    expect(
      screen.getByText("Scan your calendar before reviewing recurring events.")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Scan calendar" })).toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  })
})
