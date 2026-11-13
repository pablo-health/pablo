// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render } from "@testing-library/react"
import type { ReactNode } from "react"

// The wide-screen panel beside the sign-in form can be replaced through
// `loginBrandPanel.extensions.tsx`. With the slot empty the screen renders its
// own panel; with it filled, the replacement takes the panel's place and the
// form around it is untouched.

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
      {children}
    </div>
  ),
  AuthFooter: () => null,
  AuthHeader: () => null,
  CredentialBlock: ({ renderShell }: { renderShell: (form: ReactNode) => ReactNode }) => (
    <>{renderShell(<form data-testid="form" />)}</>
  ),
}))
vi.mock("@/components/theme/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }))

async function renderScreen() {
  const { FirebaseLoginScreen } = await import("../LoginScreen")
  return render(<FirebaseLoginScreen />)
}

describe("login brand panel slot", () => {
  afterEach(() => {
    vi.resetModules()
    vi.doUnmock("@/components/auth/loginBrandPanel.extensions")
  })

  it("renders the screen's own panel when the slot is empty", async () => {
    const { getByTestId } = await renderScreen()

    const panel = getByTestId("brand-panel")
    expect(panel.textContent).toContain("Pablo, the friendly bear in a tie")
    expect(panel.textContent).toContain("Theme")
  })

  it("renders the replacement in the panel's place, and keeps the form", async () => {
    vi.doMock("@/components/auth/loginBrandPanel.extensions", () => ({
      LoginBrandPanelExtension: () => <p>replacement panel</p>,
    }))

    const { getByTestId } = await renderScreen()

    const panel = getByTestId("brand-panel")
    expect(panel.textContent).toBe("replacement panel")
    expect(getByTestId("form")).toBeTruthy()
  })
})
