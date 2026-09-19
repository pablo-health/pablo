// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The seam that stopped a wizard walking past what was typed on the step.
 *
 * A setup step mounts the settings card for the facts it collects, and that
 * card saves on its own button. Inside a wizard there is a larger, primary
 * button below it reading Continue, and that is the one people press — so
 * everything typed was dropped on the way to the next step, silently, and
 * surfaced as "the wizard doesn't save anything" days later in Settings.
 *
 * What is worth pinning here is not the plumbing but the three rules the
 * plumbing exists to hold.
 */

import { render, screen, waitFor } from "@testing-library/react"
import { useState } from "react"
import { describe, expect, it, vi } from "vitest"
import { StepSaveProvider, useRegisterStepSave, useStepSave } from "../StepSave"

function Card({
  save,
  id = "card",
  dirty = true,
}: {
  save: () => Promise<void>
  id?: string
  dirty?: boolean
}) {
  useRegisterStepSave(id, save, dirty)
  return <p>{id}</p>
}

function Wizard({ children, onLeave }: { children: React.ReactNode; onLeave?: () => void }) {
  const saveStep = useStepSave()
  const [left, setLeft] = useState(false)

  return (
    <>
      {children}
      <button
        onClick={() =>
          void saveStep().then(
            () => {
              setLeft(true)
              onLeave?.()
            },
            () => {},
          )
        }
      >
        Continue
      </button>
      {left && <p>next step</p>}
    </>
  )
}

function clickContinue() {
  screen.getByRole("button", { name: "Continue" }).click()
}

describe("committing the step before leaving it", () => {
  it("saves the form standing on the step", async () => {
    const save = vi.fn(() => Promise.resolve())

    render(
      <StepSaveProvider>
        <Wizard>
          <Card save={save} />
        </Wizard>
      </StepSaveProvider>,
    )

    clickContinue()

    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(await screen.findByText("next step")).toBeInTheDocument()
  })

  it("stays on the step when the save is refused", async () => {
    // The card is already showing why, beside the field it belongs to. Moving
    // on would mean the ending speaks for a tax id the server rejected.
    const save = vi.fn(() => Promise.reject(new Error("nope")))

    render(
      <StepSaveProvider>
        <Wizard>
          <Card save={save} />
        </Wizard>
      </StepSaveProvider>,
    )

    clickContinue()

    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(screen.queryByText("next step")).not.toBeInTheDocument()
  })

  it("does not save a form with nothing in it", async () => {
    // Committing the step is not the same as gating it. An untouched step must
    // behave exactly as Continue always did.
    const save = vi.fn(() => Promise.resolve())

    render(
      <StepSaveProvider>
        <Wizard>
          <Card save={save} dirty={false} />
        </Wizard>
      </StepSaveProvider>,
    )

    clickContinue()

    expect(await screen.findByText("next step")).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })

  it("forgets a step once it is left behind", async () => {
    // Otherwise the next step's Continue runs the previous step's save, over
    // fields that are no longer on screen.
    const save = vi.fn(() => Promise.resolve())
    const { rerender } = render(
      <StepSaveProvider>
        <Wizard>
          <Card save={save} />
        </Wizard>
      </StepSaveProvider>,
    )

    rerender(
      <StepSaveProvider>
        <Wizard>{null}</Wizard>
      </StepSaveProvider>,
    )
    clickContinue()

    expect(await screen.findByText("next step")).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })

  it("is inert with no wizard around it, which is how Settings is unchanged", async () => {
    const save = vi.fn(() => Promise.resolve())

    render(
      <Wizard>
        <Card save={save} />
      </Wizard>,
    )

    clickContinue()

    expect(await screen.findByText("next step")).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })
})
