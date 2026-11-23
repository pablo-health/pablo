// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The invitation page: the token from the fragment survives sign-in, the
 * redeem is asked before anything else, and each refusal reads as product
 * copy rather than its code.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "@/lib/api/client"

const { previewInvite, redeemInvite, replace, signOutAndClear, auth } = vi.hoisted(() => ({
  previewInvite: vi.fn(),
  redeemInvite: vi.fn(),
  replace: vi.fn(),
  signOutAndClear: vi.fn(),
  auth: { user: null as { uid: string } | null, loading: false },
}))

vi.mock("@/lib/api/invites", () => ({ previewInvite, redeemInvite }))
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth }))
vi.mock("@/lib/auth/signOutAndClear", () => ({ signOutAndClear }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }))
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ clear: vi.fn() }) }))

import { INVITE_TOKEN_KEY, InviteAcceptance } from "../InviteAcceptance"

const TOKEN = "tok-123"

function arriveWith(hash: string) {
  window.history.replaceState(null, "", `/invite${hash}`)
}

beforeEach(() => {
  sessionStorage.clear()
  auth.user = { uid: "u1" }
  auth.loading = false
  previewInvite.mockResolvedValue({ practice_name: "Oak Street", email: "sam@example.com" })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("InviteAcceptance", () => {
  it("keeps the token from the link and takes it out of the address bar", async () => {
    arriveWith(`#${TOKEN}`)
    render(<InviteAcceptance />)

    expect(await screen.findByRole("heading", { name: /invited to join Oak Street/ })).toBeVisible()
    expect(previewInvite).toHaveBeenCalledWith(TOKEN)
    expect(sessionStorage.getItem(INVITE_TOKEN_KEY)).toBe(TOKEN)
    expect(window.location.hash).toBe("")
  })

  it("offers a sign-in that comes back to this page when signed out", async () => {
    auth.user = null
    arriveWith(`#${TOKEN}`)
    render(<InviteAcceptance />)

    const link = await screen.findByRole("link", { name: "Sign in to accept" })
    expect(link).toHaveAttribute("href", "/login?returnTo=%2Finvite")
    expect(redeemInvite).not.toHaveBeenCalled()
  })

  it("after sign-in, redeems the kept token and opens the app", async () => {
    sessionStorage.setItem(INVITE_TOKEN_KEY, TOKEN)
    arriveWith("")
    redeemInvite.mockResolvedValue({ practice_id: "p1", practice_name: "Oak Street" })
    render(<InviteAcceptance />)

    fireEvent.click(await screen.findByRole("button", { name: "Join Oak Street" }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"))
    expect(redeemInvite).toHaveBeenCalledWith(TOKEN, false)
    expect(sessionStorage.getItem(INVITE_TOKEN_KEY)).toBeNull()
  })

  it("shows both addresses on a mismatch, and confirming redeems with the account's", async () => {
    arriveWith(`#${TOKEN}`)
    redeemInvite.mockRejectedValueOnce(
      new ApiError(
        "EMAIL_MISMATCH",
        "You're signed in with a different email than the one invited",
        { invited_email: "sam@example.com", account_email: "sam@work.example" },
        409,
      ),
    )
    redeemInvite.mockResolvedValueOnce({ practice_id: "p1", practice_name: "Oak Street" })
    render(<InviteAcceptance />)

    fireEvent.click(await screen.findByRole("button", { name: "Join Oak Street" }))

    expect(
      await screen.findByText(/sent to sam@example\.com, and you’re signed in as sam@work\.example/),
    ).toBeVisible()
    expect(screen.queryByText(/EMAIL_MISMATCH/)).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Sign in as sam@example.com instead" }))
    expect(signOutAndClear).toHaveBeenCalledWith(
      expect.anything(),
      expect.anything(),
      "/login?returnTo=%2Finvite",
    )

    fireEvent.click(screen.getByRole("button", { name: "Join as sam@work.example" }))
    await waitFor(() => expect(redeemInvite).toHaveBeenLastCalledWith(TOKEN, true))
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"))
  })

  it("says a spent link can't be used, and forgets it", async () => {
    arriveWith(`#${TOKEN}`)
    previewInvite.mockRejectedValue(
      new ApiError("INVITE_UNAVAILABLE", "gone", undefined, 410),
    )
    render(<InviteAcceptance />)

    expect(await screen.findByRole("heading", { name: "This invitation can’t be used" })).toBeVisible()
    expect(sessionStorage.getItem(INVITE_TOKEN_KEY)).toBeNull()
  })

  it("explains an account that already has a practice without its code", async () => {
    arriveWith(`#${TOKEN}`)
    redeemInvite.mockRejectedValue(
      new ApiError("ALREADY_IN_PRACTICE", "already", undefined, 409),
    )
    render(<InviteAcceptance />)

    fireEvent.click(await screen.findByRole("button", { name: "Join Oak Street" }))

    expect(
      await screen.findByRole("heading", { name: "This account already belongs to a practice" }),
    ).toBeVisible()
    expect(screen.getByText("To join Oak Street, sign in with a different account.")).toBeVisible()
    expect(screen.queryByText(/ALREADY_IN_PRACTICE/)).toBeNull()
  })

  it("says the link doesn't work when there is no token", async () => {
    arriveWith("")
    render(<InviteAcceptance />)

    expect(
      await screen.findByRole("heading", { name: "This invitation link doesn’t work" }),
    ).toBeVisible()
    expect(previewInvite).not.toHaveBeenCalled()
  })
})
