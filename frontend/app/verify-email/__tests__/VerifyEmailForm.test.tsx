// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

const push = vi.hoisted(() => vi.fn())
const sendEmailVerification = vi.hoisted(() => vi.fn())
const getFirebaseAuth = vi.hoisted(() => vi.fn())
const signOutAndClear = vi.hoisted(() => vi.fn())
const clear = vi.hoisted(() => vi.fn())

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))
vi.mock("firebase/auth", () => ({ sendEmailVerification }))
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth }))
vi.mock("@/lib/auth/signOutAndClear", () => ({ signOutAndClear }))
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ clear }) }))

import { VerifyEmailForm } from "../VerifyEmailForm"

function fakeUser(verifiedAfterReload: boolean) {
  const user = {
    emailVerified: false,
    refreshToken: "refresh",
    reload: vi.fn(async () => {
      user.emailVerified = verifiedAfterReload
    }),
    getIdToken: vi.fn().mockResolvedValue("fresh-token"),
  }
  return user
}

function useUser(user: ReturnType<typeof fakeUser> | null) {
  getFirebaseAuth.mockReturnValue({
    authStateReady: vi.fn().mockResolvedValue(undefined),
    currentUser: user,
  })
}

describe("VerifyEmailForm", () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("shows the account email", () => {
    useUser(fakeUser(false))
    render(<VerifyEmailForm email="clinician@example.com" />)
    expect(screen.getByText("clinician@example.com")).toBeInTheDocument()
  })

  it("sends a new verification link that returns to the dashboard", async () => {
    const user = fakeUser(false)
    useUser(user)
    render(<VerifyEmailForm email="clinician@example.com" />)

    await userEvent.click(screen.getByRole("button", { name: "Send a new link" }))

    expect(sendEmailVerification).toHaveBeenCalledWith(user, {
      url: `${window.location.origin}/dashboard`,
    })
    expect(await screen.findByText(/Check your inbox/)).toBeInTheDocument()
  })

  it("re-seeds the session with a fresh token once verified, then opens the dashboard", async () => {
    const user = fakeUser(true)
    useUser(user)
    render(<VerifyEmailForm email="clinician@example.com" />)

    await userEvent.click(screen.getByRole("button", { name: "I’ve verified it" }))

    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"))
    expect(user.getIdToken).toHaveBeenCalledWith(true)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/login",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer fresh-token" }),
      }),
    )
  })

  it("stays put and says so when the email is still unverified", async () => {
    useUser(fakeUser(false))
    render(<VerifyEmailForm email="clinician@example.com" />)

    await userEvent.click(screen.getByRole("button", { name: "I’ve verified it" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("isn’t verified yet")
    expect(push).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("shows the provider's message when sending fails", async () => {
    useUser(fakeUser(false))
    sendEmailVerification.mockRejectedValueOnce({ code: "auth/too-many-requests" })
    render(<VerifyEmailForm email="clinician@example.com" />)

    await userEvent.click(screen.getByRole("button", { name: "Send a new link" }))

    expect(await screen.findByRole("alert")).toBeInTheDocument()
    expect(screen.queryByText(/Check your inbox/)).not.toBeInTheDocument()
  })
})
