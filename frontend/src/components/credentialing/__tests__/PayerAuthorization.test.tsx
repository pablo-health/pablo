// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The permission slip that lets Pablo sign her name to a payer's form.
 *
 * Bug classes covered:
 *   * a self-hoster asked to sign for a service she isn't buying. `available`
 *     false has to render NOTHING, not an empty card or a disabled button.
 *   * a re-signature reading as lost paperwork. Somebody who already agreed
 *     once is not being asked from scratch, and the heading has to say which.
 *   * signing what she cannot read. The text failing to load must not leave a
 *     live Sign button beside it.
 *   * withdrawing by mis-click. It stops us mid-application, so it takes two
 *     steps and the second one states the consequence.
 *   * the sign button firing with an empty signature.
 */

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PayerAuthorization } from "../PayerAuthorization"
import type { PayerAuthorizationStatus } from "@/types/credentialing"

const usePayerAuthorization = vi.hoisted(() => vi.fn())
const usePayerAuthorizationDocument = vi.hoisted(() => vi.fn())
const sign = vi.hoisted(() => vi.fn())
const revoke = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useCredentialingChecklist", () => ({
  usePayerAuthorization: () => usePayerAuthorization(),
  usePayerAuthorizationDocument: (...args: unknown[]) =>
    usePayerAuthorizationDocument(...args),
  useSignPayerAuthorization: () => ({ mutate: sign, isPending: false, isError: false }),
  useRevokePayerAuthorization: () => ({ mutate: revoke, isPending: false }),
}))

function status(overrides: Partial<PayerAuthorizationStatus> = {}) {
  usePayerAuthorization.mockReturnValue({
    data: {
      available: true,
      current_version: "2026-09-13",
      signed: false,
      signed_version: null,
      signed_at: null,
      signed_name: null,
      superseded: false,
      ...overrides,
    },
    isLoading: false,
  })
}

beforeEach(() => {
  usePayerAuthorization.mockReset()
  usePayerAuthorizationDocument.mockReset()
  sign.mockReset()
  revoke.mockReset()
  usePayerAuthorizationDocument.mockReturnValue({
    data: "The authorisation text.",
    isLoading: false,
    isError: false,
  })
})

describe("a deployment that bundles no authorisation", () => {
  it("shows nothing at all", () => {
    usePayerAuthorization.mockReturnValue({
      data: { available: false, signed: false },
      isLoading: false,
    })

    const { container } = render(<PayerAuthorization />)

    expect(container).toBeEmptyDOMElement()
  })

  it("shows nothing while the answer is still in flight, rather than flashing a form", () => {
    usePayerAuthorization.mockReturnValue({ data: undefined, isLoading: true })

    const { container } = render(<PayerAuthorization />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe("before she signs", () => {
  it("says what she is actually authorising, in concrete terms", () => {
    status()

    render(<PayerAuthorization />)

    expect(screen.getByText(/let pablo apply on your behalf/i)).toBeInTheDocument()
    expect(screen.getByText(/signing your name to the payer/i)).toBeInTheDocument()
  })

  it("will not sign with an empty signature", () => {
    status()

    render(<PayerAuthorization />)

    expect(screen.getByRole("button", { name: /^sign$/i })).toBeDisabled()
  })

  it("signs the version she was shown, not whatever is current at click time", async () => {
    status({ current_version: "2026-09-13" })

    render(<PayerAuthorization />)
    await userEvent.type(screen.getByLabelText(/type your name/i), "Ana Rivera")
    await userEvent.click(screen.getByRole("button", { name: /^sign$/i }))

    expect(sign).toHaveBeenCalledWith({
      version: "2026-09-13",
      signed_name: "Ana Rivera",
      accepted: true,
    })
  })

  it("trims the signature rather than storing the stray space", async () => {
    status()

    render(<PayerAuthorization />)
    await userEvent.type(screen.getByLabelText(/type your name/i), "  Ana Rivera  ")
    await userEvent.click(screen.getByRole("button", { name: /^sign$/i }))

    expect(sign).toHaveBeenCalledWith(
      expect.objectContaining({ signed_name: "Ana Rivera" }),
    )
  })

  it("lets her read it before deciding", async () => {
    status()

    render(<PayerAuthorization />)
    await userEvent.click(screen.getByRole("button", { name: /read the authorisation/i }))

    expect(screen.getByText("The authorisation text.")).toBeInTheDocument()
  })

  it("does not fetch the document until she asks for it", () => {
    status()

    render(<PayerAuthorization />)

    expect(usePayerAuthorizationDocument).not.toHaveBeenCalled()
  })

  it("says so when the text will not load, rather than letting her sign blind", async () => {
    status()
    usePayerAuthorizationDocument.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    })

    render(<PayerAuthorization />)
    await userEvent.click(screen.getByRole("button", { name: /read the authorisation/i }))

    expect(screen.getByText(/don’t sign what you can’t read/i)).toBeInTheDocument()
  })
})

describe("when the wording has changed since she signed", () => {
  it("says it was updated, not that she never signed", () => {
    status({ superseded: true, signed_version: "2026-01-01" })

    render(<PayerAuthorization />)

    expect(screen.getByText(/we’ve updated this authorisation/i)).toBeInTheDocument()
    expect(screen.getByText(/you signed version 2026-01-01/i)).toBeInTheDocument()
  })

  it("still asks for a signature — an old one is history, not consent", () => {
    status({ superseded: true, signed_version: "2026-01-01" })

    render(<PayerAuthorization />)

    expect(screen.getByRole("button", { name: /^sign$/i })).toBeInTheDocument()
  })
})

describe("once she has signed", () => {
  const signed = {
    signed: true,
    signed_version: "2026-09-13",
    signed_at: "2026-09-13T12:00:00Z",
    signed_name: "Ana Rivera",
  }

  it("says what she signed and when, rather than a bare tick", () => {
    status(signed)

    render(<PayerAuthorization />)

    expect(screen.getByText(/signed Ana Rivera/i)).toBeInTheDocument()
    expect(screen.getByText(/version 2026-09-13/i)).toBeInTheDocument()
  })

  it("offers no signing form", () => {
    status(signed)

    render(<PayerAuthorization />)

    expect(screen.queryByLabelText(/type your name/i)).not.toBeInTheDocument()
  })

  it("takes two steps to withdraw, and states the consequence on the second", async () => {
    status(signed)

    render(<PayerAuthorization />)
    await userEvent.click(screen.getByRole("button", { name: /withdraw it/i }))

    expect(screen.getByText(/stop work on every application in progress/i)).toBeInTheDocument()
    expect(revoke).not.toHaveBeenCalled()
  })

  it("withdraws on the second step", async () => {
    status(signed)

    render(<PayerAuthorization />)
    await userEvent.click(screen.getByRole("button", { name: /withdraw it/i }))
    await userEvent.click(screen.getByRole("button", { name: /^withdraw$/i }))

    expect(revoke).toHaveBeenCalled()
  })

  it("lets her back out of withdrawing", async () => {
    status(signed)

    render(<PayerAuthorization />)
    await userEvent.click(screen.getByRole("button", { name: /withdraw it/i }))
    await userEvent.click(screen.getByRole("button", { name: /keep it/i }))

    expect(screen.queryByRole("button", { name: /^withdraw$/i })).not.toBeInTheDocument()
    expect(revoke).not.toHaveBeenCalled()
  })

  it("lets her re-read what she signed", async () => {
    status(signed)

    render(<PayerAuthorization />)
    await userEvent.click(screen.getByRole("button", { name: /read what you signed/i }))

    expect(screen.getByText("The authorisation text.")).toBeInTheDocument()
  })
})
