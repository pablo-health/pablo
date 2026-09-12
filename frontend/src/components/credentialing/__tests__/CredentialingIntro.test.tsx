// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The explainer a first-timer meets, and the door out of it.
 *
 * Two audiences on one screen: someone who has never heard the word
 * "credentialing", and someone who finished it years ago and must not be made
 * to read about it.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { CredentialingIntro } from "../CredentialingIntro"

vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => (
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
    <img {...(props as { alt?: string })} />
  ),
}))

describe("CredentialingIntro", () => {
  it("names the jargon rather than avoiding it", () => {
    // She will meet the word within a day of starting — from a payer, from
    // CAQH, from her board. Better learned here than from a rejection.
    render(<CredentialingIntro onStart={vi.fn()} />)

    expect(screen.getByText("credentialing")).toBeInTheDocument()
    expect(screen.getByText(/insurance panels/i)).toBeInTheDocument()
  })

  it("says the two steps are separate, because being verified is not being contracted", () => {
    render(<CredentialingIntro onStart={vi.fn()} />)

    expect(
      screen.getByText(/being through the first does not mean you are through the second/i),
    ).toBeInTheDocument()
  })

  it("promises she can stop, in the explainer and not only later", () => {
    render(<CredentialingIntro onStart={vi.fn()} />)

    expect(screen.getByText(/do not have to finish at all/i)).toBeInTheDocument()
  })

  it("starts the intake", () => {
    const onStart = vi.fn()
    render(<CredentialingIntro onStart={onStart} />)

    fireEvent.click(screen.getByRole("button", { name: /start/i }))

    expect(onStart).toHaveBeenCalled()
  })

  it("offers the already-paneled clinician somewhere better than here", () => {
    // Not a dismissal. Recording which payers she is contracted with is what
    // decides claim-versus-superbill on every session afterwards, so the exit
    // from this page collects the one fact worth having from her.
    render(<CredentialingIntro onStart={vi.fn()} />)

    const link = screen.getByRole("link", { name: /tell us which ones/i })
    expect(link).toHaveAttribute("href", "/dashboard/settings/insurance")
  })
})
