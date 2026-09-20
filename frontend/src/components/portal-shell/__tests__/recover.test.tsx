// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The recovery page — one field, one button, and one thing it may say.
 *
 * Everything here follows from what the endpoint behind it does: it answers
 * 202 whether or not the address belongs to anybody, because whether
 * somebody is a patient of a therapy practice is not a fact this page gets
 * to confirm to whoever typed the address.
 *
 * So the assertions below are mostly about what the page does NOT do. It
 * does not have a success message and a not-found message. It does not
 * report "no such account". It shows one conditional sentence, for every
 * address, and an error only when the request did not get through.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PortalRecover } from "../PortalRecover"

const resolvePortalPractice = vi.fn()
const requestPortalRecovery = vi.fn()

vi.mock("@/lib/portal-shell/api", () => ({
  resolvePortalPractice: (...args: unknown[]) => resolvePortalPractice(...args),
  requestPortalRecovery: (...args: unknown[]) => requestPortalRecovery(...args),
}))

const SLUG = "example-therapy"
const PINNED_COPY = "If we find a portal account for this email, we'll send a new sign-in link."

async function submit(email: string): Promise<void> {
  await userEvent.type(screen.getByTestId("portal-recover-email"), email)
  await userEvent.click(screen.getByTestId("portal-recover-submit"))
}

beforeEach(() => {
  vi.clearAllMocks()
  resolvePortalPractice.mockResolvedValue({
    ok: true,
    data: { slug: SLUG, display_name: "Example Therapy", captcha_site_key: null },
  })
  requestPortalRecovery.mockResolvedValue({ ok: true })
})

describe("the recovery page", () => {
  it("names the practice", async () => {
    render(<PortalRecover slug={SLUG} />)

    await screen.findByText("Example Therapy")
  })

  it("asks for an email address and nothing else", async () => {
    /**
     * No date of birth, no last visit, no last four digits. A knowledge
     * check gives the caller a second answer to read and gates recovery on
     * something an acquaintance usually knows.
     */
    render(<PortalRecover slug={SLUG} />)

    const fields = await screen.findAllByRole("textbox")
    expect(fields).toHaveLength(1)
    expect(screen.getByLabelText("Email")).toBeTruthy()
  })

  it("sends the address to the practice in the path", async () => {
    render(<PortalRecover slug={SLUG} />)

    await submit("ada@example.test")

    expect(requestPortalRecovery).toHaveBeenCalledWith(SLUG, "ada@example.test", null)
  })

  it("shows the pinned copy after a submission", async () => {
    render(<PortalRecover slug={SLUG} />)

    await submit("ada@example.test")

    expect((await screen.findByTestId("portal-recover-sent")).textContent).toBe(PINNED_COPY)
  })

  it("shows the same copy for an address nobody here has", async () => {
    /**
     * The page cannot tell the difference and must not appear to: the
     * server answered 202 both times.
     */
    render(<PortalRecover slug={SLUG} />)

    await submit("stranger@example.test")

    expect((await screen.findByTestId("portal-recover-sent")).textContent).toBe(PINNED_COPY)
  })

  it("never claims a link was sent", async () => {
    render(<PortalRecover slug={SLUG} />)
    await submit("ada@example.test")
    await screen.findByTestId("portal-recover-sent")

    const body = document.body.textContent ?? ""
    expect(body).not.toContain("We've sent")
    expect(body).not.toContain("Check your inbox")
    expect(body).not.toContain("No account")
  })

  it("announces the confirmation", async () => {
    /** The person who just submitted may not be looking at that corner. */
    render(<PortalRecover slug={SLUG} />)

    await submit("ada@example.test")

    const live = await screen.findByRole("status")
    expect(live.textContent).toContain("If we find a portal account")
  })

  it("reports a request that did not get through", async () => {
    /**
     * `ok: false` means the request was refused or never arrived — a
     * closed rate-limit window, a network failure. It never means "no such
     * patient", which the page cannot learn.
     */
    requestPortalRecovery.mockResolvedValue({ ok: false })
    render(<PortalRecover slug={SLUG} />)

    await submit("ada@example.test")

    await screen.findByTestId("portal-recover-failed")
    expect(screen.queryByTestId("portal-recover-sent")).toBeNull()
  })

  it("keeps the submit button disabled until an address is typed", async () => {
    render(<PortalRecover slug={SLUG} />)

    const button = await screen.findByTestId("portal-recover-submit")
    expect(button.hasAttribute("disabled")).toBe(true)

    await userEvent.type(screen.getByTestId("portal-recover-email"), "a@b.test")
    expect(button.hasAttribute("disabled")).toBe(false)
  })

  it("offers a way back to the portal", async () => {
    render(<PortalRecover slug={SLUG} />)

    const back = await screen.findByTestId("portal-recover-back")
    expect(back.getAttribute("href")).toBe(`/portal/${SLUG}`)
  })

  it("associates the label with the field", async () => {
    render(<PortalRecover slug={SLUG} />)

    const field = await screen.findByLabelText("Email")
    expect(field.getAttribute("id")).toBe("portal-recover-email")
  })

  it("is walkable by keyboard from the field to the button", async () => {
    render(<PortalRecover slug={SLUG} />)
    const field = await screen.findByTestId("portal-recover-email")

    field.focus()
    expect(document.activeElement).toBe(field)
    await userEvent.type(field, "ada@example.test")
    await userEvent.tab()

    expect(document.activeElement).toBe(screen.getByTestId("portal-recover-submit"))
  })

  it("renders no widget when the deployment has no CAPTCHA provider", async () => {
    render(<PortalRecover slug={SLUG} />)

    await screen.findByTestId("portal-recover-submit")
    expect(document.querySelector("script[src*='turnstile']")).toBeNull()
  })

  it("still works when the practice cannot be resolved", async () => {
    /**
     * The form is the point of the page; the header is decoration. A
     * resolve that failed should not deny recovery to somebody whose link
     * has expired.
     */
    resolvePortalPractice.mockResolvedValue({ ok: false })
    render(<PortalRecover slug={SLUG} />)

    await submit("ada@example.test")

    await screen.findByTestId("portal-recover-sent")
  })
})
