// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The service code field, where a therapist actually meets it.
 *
 * It sits beside the fee because that is where rates are already edited, and
 * this is the one component both Settings and the billing setup wizard mount.
 *
 * Bug classes covered:
 *   * the field arriving pre-filled — a code we chose from the type's length
 *     or its name is worse than no code, because she will never look at it;
 *   * the field becoming a gate, when setup is progressive by design;
 *   * tabbing through a settings page writing to the practice's billing setup;
 *   * the suggestions hardening into an allow-list, so the one payer who wants
 *     something unusual has to come and ask us for it.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AppointmentTypeRow } from "../AppointmentTypeRow"
import type { AppointmentTypeResponse } from "@/types/scheduling"

function makeType(overrides: Partial<AppointmentTypeResponse> = {}): AppointmentTypeResponse {
  return {
    id: "type_session",
    user_id: "user_1",
    name: "Session",
    default_fee_cents: 16000,
    duration_minutes: 50,
    cpt: null,
    audience: "existing",
    min_notice_hours: null,
    earliest_offer_business_days: 1,
    horizon: 10,
    horizon_unit: "business",
    self_bookable: false,
    offerable: true,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

function renderRow(overrides: Partial<AppointmentTypeResponse> = {}) {
  const onChange = vi.fn()
  render(
    <ul>
      <AppointmentTypeRow
        appointmentType={makeType(overrides)}
        open
        onToggle={vi.fn()}
        onChange={onChange}
        onDelete={vi.fn()}
        selfBookOn={false}
        defaultNoticeHours={24}
      />
    </ul>
  )
  return { onChange, field: screen.getByLabelText(/service code/i) }
}

describe("the service code field", () => {
  it("starts empty on a 50-minute session rather than guessing 90834", () => {
    const { field } = renderRow({ duration_minutes: 50 })

    expect(field).toHaveValue("")
  })

  it("starts empty on an intake rather than guessing 90791", () => {
    const { field } = renderRow({ name: "Intake", duration_minutes: 60, audience: "new" })

    expect(field).toHaveValue("")
  })

  it("says out loud that leaving it blank is fine", () => {
    renderRow()

    expect(screen.getByText(/leave it blank until you need it/i)).toBeInTheDocument()
  })

  it("shows the code the practice already set", () => {
    const { field } = renderRow({ cpt: "90837" })

    expect(field).toHaveValue("90837")
  })

  it("names a code it recognises, so she can see she picked the right one", () => {
    renderRow({ cpt: "90837" })

    // The suggestion list carries the same words, so look for the hint under
    // the field rather than anywhere on the page.
    const hints = screen.getAllByText(/60 minutes or more/i)
    expect(hints.some((el) => el.tagName === "SMALL")).toBe(true)
  })

  it("saves what she typed", async () => {
    const user = userEvent.setup()
    const { onChange, field } = renderRow()

    await user.type(field, "90837")
    await user.tab()

    expect(onChange).toHaveBeenCalledWith({ cpt: "90837" })
  })

  it("accepts a code that is on no list of ours", async () => {
    const user = userEvent.setup()
    const { onChange, field } = renderRow()

    await user.type(field, "T1015")
    await user.tab()

    expect(onChange).toHaveBeenCalledWith({ cpt: "T1015" })
  })

  it("clears the code when she empties the field", async () => {
    const user = userEvent.setup()
    const { onChange, field } = renderRow({ cpt: "90837" })

    await user.clear(field)
    await user.tab()

    expect(onChange).toHaveBeenCalledWith({ cpt: null })
  })

  it("writes nothing when she only tabs through", async () => {
    const user = userEvent.setup()
    const { onChange, field } = renderRow({ cpt: "90837" })

    await user.click(field)
    await user.tab()

    expect(onChange).not.toHaveBeenCalled()
  })

  it("offers the common codes without requiring one of them", () => {
    const { field } = renderRow()

    const listId = field.getAttribute("list")
    expect(listId).toBeTruthy()
    const list = document.getElementById(listId as string)
    expect(list?.querySelectorAll("option").length).toBeGreaterThan(0)
    expect(field).not.toBeRequired()
  })
})
