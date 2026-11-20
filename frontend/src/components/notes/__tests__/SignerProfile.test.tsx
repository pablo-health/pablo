// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A signature's name and credentials are kept on the clinician's profile once
 * the signature is stored, so the next dialog starts from them. A cancelled or
 * failed signature keeps nothing, and an account whose only name is its email
 * address starts with the name empty.
 *
 * These run the real dialogs and the real hooks; only the user API is a
 * stand-in, an in-memory profile that answers the way the server does.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AddAddendumDialog, SignNoteDialog } from "../signing/SigningDialogs"
import type { NoteSignerFields } from "@/types/notes"

const server = vi.hoisted(() => ({
  profile: { name: "", email: "", credentials: null as string | null },
  updates: [] as Record<string, unknown>[],
  refuseUpdate: false,
}))

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: vi.fn(async () => ({ ...server.profile })),
  updateUserProfile: vi.fn(async (data: { name?: string; credentials?: string }) => {
    server.updates.push(data)
    if (server.refuseUpdate) throw new Error("refused")
    if (data.name !== undefined) server.profile.name = data.name
    if (data.credentials !== undefined) server.profile.credentials = data.credentials
    return { ...server.profile }
  }),
}))
vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
}))

beforeEach(() => {
  server.profile = { name: "sam@example.com", email: "sam@example.com", credentials: null }
  server.updates = []
  server.refuseUpdate = false
})

afterEach(() => {
  vi.clearAllMocks()
})

/** A signing page: the dialog opens and closes; a fresh client is a reload. */
function signingPage(onSign: (signer: NoteSignerFields) => Promise<unknown>) {
  let client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = (open: boolean) => (
    <QueryClientProvider client={client}>
      <SignNoteDialog open={open} onOpenChange={(next) => view.rerender(ui(next))} onSign={onSign} />
    </QueryClientProvider>
  )
  const view = render(ui(true))
  return {
    open: () => view.rerender(ui(true)),
    reload: () => {
      view.unmount()
      client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      Object.assign(view, render(ui(true)))
    },
  }
}

const nameField = () => screen.findByLabelText("Your name")
const credentialsField = () => screen.getByLabelText("Credentials")
const dialogGone = () => waitFor(() => expect(screen.queryByLabelText("Your name")).toBeNull())

async function fill(name: string, credentials: string) {
  const user = userEvent.setup()
  await user.clear(await nameField())
  if (name) await user.type(await nameField(), name)
  await user.clear(credentialsField())
  if (credentials) await user.type(credentialsField(), credentials)
  return user
}

describe("signer fields kept on the profile", () => {
  it("starts an email-only account with an empty name, never the email", async () => {
    signingPage(async () => {})
    expect(await nameField()).toHaveValue("")
    expect(credentialsField()).toHaveValue("")
    expect(screen.getByRole("button", { name: "Sign and lock" })).toBeDisabled()
  })

  it("starts the next dialog from what was signed with, and after a reload", async () => {
    const onSign = vi.fn(async () => {})
    const page = signingPage(onSign)
    const user = await fill("Sam Ortiz", "LMFT")
    await user.click(screen.getByRole("button", { name: "Sign and lock" }))

    // The signature carries exactly what was in the dialog.
    expect(onSign).toHaveBeenCalledWith({ signer_name: "Sam Ortiz", signer_credentials: "LMFT" })
    await dialogGone()
    await waitFor(() => expect(server.updates).toEqual([{ name: "Sam Ortiz", credentials: "LMFT" }]))

    page.open()
    expect(await nameField()).toHaveValue("Sam Ortiz")
    expect(credentialsField()).toHaveValue("LMFT")

    page.reload()
    expect(await nameField()).toHaveValue("Sam Ortiz")
    expect(credentialsField()).toHaveValue("LMFT")
  })

  it("keeps a change made in a later dialog, and writes only what changed", async () => {
    server.profile = { name: "Sam Ortiz", email: "sam@example.com", credentials: "LMFT" }
    const page = signingPage(async () => {})
    expect(await nameField()).toHaveValue("Sam Ortiz")
    const user = await fill("Sam Ortiz", "PhD, LMFT")
    await user.click(screen.getByRole("button", { name: "Sign and lock" }))
    await dialogGone()
    await waitFor(() => expect(server.updates).toEqual([{ credentials: "PhD, LMFT" }]))

    page.reload()
    expect(await nameField()).toHaveValue("Sam Ortiz")
    expect(credentialsField()).toHaveValue("PhD, LMFT")
  })

  it("writes nothing when the dialog is signed as it opened", async () => {
    server.profile = { name: "Sam Ortiz", email: "sam@example.com", credentials: "LMFT" }
    const onSign = vi.fn(async () => {})
    signingPage(onSign)
    await nameField()
    await userEvent.setup().click(screen.getByRole("button", { name: "Sign and lock" }))
    await dialogGone()
    expect(onSign).toHaveBeenCalledWith({ signer_name: "Sam Ortiz", signer_credentials: "LMFT" })
    expect(server.updates).toEqual([])
  })

  it("keeps nothing from a cancelled dialog", async () => {
    server.profile = { name: "Sam Ortiz", email: "sam@example.com", credentials: "LMFT" }
    const onSign = vi.fn(async () => {})
    const page = signingPage(onSign)
    const user = await fill("Someone Else", "MD")
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    await dialogGone()
    expect(onSign).not.toHaveBeenCalled()
    expect(server.updates).toEqual([])

    page.open()
    expect(await nameField()).toHaveValue("Sam Ortiz")
    expect(credentialsField()).toHaveValue("LMFT")
  })

  it("keeps nothing from a signature that failed", async () => {
    server.profile = { name: "Sam Ortiz", email: "sam@example.com", credentials: "LMFT" }
    const page = signingPage(async () => {
      throw new Error("The note changed while you were signing it.")
    })
    const user = await fill("Someone Else", "MD")
    await user.click(screen.getByRole("button", { name: "Sign and lock" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("The note changed")
    expect(server.updates).toEqual([])

    await user.click(screen.getByRole("button", { name: "Cancel" }))
    await dialogGone()
    page.open()
    expect(await nameField()).toHaveValue("Sam Ortiz")
    expect(credentialsField()).toHaveValue("LMFT")
  })

  it("lets the signature stand when the profile write is refused", async () => {
    server.refuseUpdate = true
    const onSign = vi.fn(async () => {})
    const page = signingPage(onSign)
    const user = await fill("Sam Ortiz", "LMFT")
    await user.click(screen.getByRole("button", { name: "Sign and lock" }))
    await dialogGone()
    expect(onSign).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(server.updates).toHaveLength(1))

    // The re-read puts the profile's own values back.
    page.reload()
    expect(await nameField()).toHaveValue("")
  })

  it("keeps an addendum's signer fields the same way", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const onAdd = vi.fn(async () => {})
    render(
      <QueryClientProvider client={client}>
        <AddAddendumDialog open onOpenChange={() => {}} onAdd={onAdd} initialText="Called back." />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByLabelText("Your name")).toHaveValue(""))
    const user = await fill("Sam Ortiz", "LMFT")
    await user.click(screen.getByRole("button", { name: "Sign and add" }))
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1))
    expect(onAdd).toHaveBeenCalledWith("Called back.", {
      signer_name: "Sam Ortiz",
      signer_credentials: "LMFT",
    })
    await waitFor(() => expect(server.updates).toEqual([{ name: "Sam Ortiz", credentials: "LMFT" }]))
  })
})
