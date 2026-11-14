// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render } from "@testing-library/react"
import type { ReactNode } from "react"

// A message can sit at the top of the sign-in form through
// `loginTopMessage.extensions.tsx`. With the slot empty the form starts with
// its heading as before; with it filled, the message comes first in the card,
// outside the wide-screen panel, so it shows at every width.

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }))
vi.mock("next/image", () => ({ default: ({ alt }: { alt: string }) => <span>{alt}</span> }))
vi.mock("@/lib/firebase", () => ({ getFirebaseAuth: vi.fn() }))
vi.mock("../client", () => ({ clearStaleSession: vi.fn() }))
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: null, loading: true }) }))
vi.mock("@/lib/firebaseAuthRecovery", () => ({
  installAuthRecovery: vi.fn(),
  consumeRecoveryNotice: () => false,
}))
vi.mock("@/components/auth", () => ({
  AuthCard: ({ children, brandPanel }: { children: ReactNode; brandPanel?: ReactNode }) => (
    <div>
      <aside data-testid="brand-panel">{brandPanel}</aside>
      <main data-testid="card">{children}</main>
    </div>
  ),
  AuthFooter: () => null,
  AuthHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
  CredentialBlock: ({ renderShell }: { renderShell: (form: ReactNode) => ReactNode }) => (
    <>{renderShell(<form data-testid="form" />)}</>
  ),
}))
vi.mock("@/components/theme/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }))

async function renderScreen() {
  const { FirebaseLoginScreen } = await import("../LoginScreen")
  return render(<FirebaseLoginScreen />)
}

describe("login top message slot", () => {
  afterEach(() => {
    vi.resetModules()
    vi.doUnmock("@/components/auth/loginTopMessage.extensions")
  })

  it("leaves the card as it was when the slot is empty", async () => {
    const { getByTestId } = await renderScreen()

    const card = getByTestId("card")
    expect(card.firstElementChild?.tagName).toBe("DIV")
    expect(card.firstElementChild?.textContent).toContain("Pablo's got it.")
    expect(card.querySelector("[data-testid='top-message']")).toBeNull()
  })

  it("puts the message first in the card, before the heading and the form", async () => {
    vi.doMock("@/components/auth/loginTopMessage.extensions", () => ({
      LoginTopMessageExtension: () => <p data-testid="top-message">Sign-in is slow right now.</p>,
    }))

    const { getByTestId } = await renderScreen()

    const card = getByTestId("card")
    expect(card.firstElementChild).toBe(getByTestId("top-message"))
    const heading = card.querySelector("h1")
    expect(heading?.textContent).toBe("Welcome back")
    expect(
      getByTestId("top-message").compareDocumentPosition(heading!) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(getByTestId("brand-panel").textContent).not.toContain("Sign-in is slow")
    expect(getByTestId("form")).toBeTruthy()
  })
})
