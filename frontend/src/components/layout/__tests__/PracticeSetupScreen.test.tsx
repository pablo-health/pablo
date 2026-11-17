// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PracticeSetupScreen covers the page while a request waits for the practice
 * to be set up, and goes once that request is answered. A real apiClient call
 * drives it, against a fetch that answers 503 TENANT_PROVISIONING_PENDING
 * once and then 200.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { useEffect, useState } from "react"
import { PracticeSetupScreen } from "../PracticeSetupScreen"
import { get, setApiUrl } from "@/lib/api/client"
import { resetPracticeSetupForTests } from "@/lib/api/practiceSetup"

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken: vi.fn().mockResolvedValue("tok"), signOut: vi.fn() }),
}))
vi.mock("@/lib/api/client.extensions", () => ({ apiErrorInterceptors: [] }))

const fetchMock = vi.fn()

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  })
}

function Practice() {
  const [name, setName] = useState<string | null>(null)
  useEffect(() => {
    void get<{ name: string }>("/api/practice").then((p) => setName(p.name))
  }, [])
  return name ? <p>{name}</p> : null
}

beforeEach(() => {
  resetPracticeSetupForTests()
  setApiUrl("http://test")
  fetchMock.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("PracticeSetupScreen", () => {
  it("renders nothing when no request is waiting", () => {
    const { container } = render(<PracticeSetupScreen />)
    expect(container).toBeEmptyDOMElement()
  })

  it("shows the setup state for a pending practice, then the app once a request succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce(
        json(
          503,
          {
            detail: {
              error: {
                code: "TENANT_PROVISIONING_PENDING",
                message: "Your practice is being set up.",
                details: { practice_id: "p1", provisioning_status: "pending" },
              },
            },
          },
          { "retry-after": "1" },
        ),
      )
      .mockResolvedValueOnce(json(200, { name: "Riverside Counseling" }))

    render(
      <>
        <PracticeSetupScreen />
        <Practice />
      </>,
    )

    const setupScreen = await screen.findByTestId("practice-setup-screen")
    expect(setupScreen).toHaveTextContent("Setting up your practice")
    // The state never claims the practice is ready; only the answer shows that.
    expect(setupScreen.textContent ?? "").not.toMatch(/ready|done|finished|complete/i)

    await waitFor(() => expect(screen.getByText("Riverside Counseling")).toBeInTheDocument(), {
      timeout: 3000,
    })
    expect(screen.queryByTestId("practice-setup-screen")).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
