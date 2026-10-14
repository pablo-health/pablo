// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A passkey sign-in stays signed in.
 *
 * A clinician who signed in with a passkey sometimes landed on the dashboard
 * signed out: the page's first requests went out with no token, the session
 * cookie was cleared, and every link led back to /login. Signing in a second
 * time worked.
 *
 * The cause was the login screen's cleanup after a forced logout (an idle
 * timeout or an expired session). It wiped the browser's stored sign-in by
 * deleting Firebase's IndexedDB database, but the page's own Firebase
 * connection holds that database open, so the delete could not run. It waited
 * — and ran the next time the page unloaded, by which point it was deleting
 * the passkey sign-in that had just been stored. The next full page load
 * found no one signed in.
 *
 * So the journey here is the one that tripped it: arrive on /login from a
 * forced logout, sign in with a passkey, land on the dashboard, then load the
 * page again from scratch. Alternate rounds sign out from the menu instead, so
 * the ordinary path is covered too. Twenty rounds, because the clinician who
 * reported it hit it on some sign-ins and not others.
 *
 * The browser's passkey is a CDP virtual authenticator, so the real ceremony
 * runs end to end: the product's own WebAuthn call, the API's verification,
 * the custom-token sign-in and the session cookie.
 */

import type { CDPSession, Page } from "@playwright/test"
import { ApiClient, createEmulatorUser, signInWithPassword } from "../fixtures/api"
import { expect, test } from "../fixtures/auth"

const ROUNDS = 20

/**
 * Its own account, not the worker's clinician: a passkey on that account
 * would put every password sign-in in the suite through passkey step-up. New
 * on every attempt, because the virtual authenticator is: a retry reusing an
 * address would find a passkey registered on a browser that no longer exists,
 * and the API refuses to add another without asserting that one.
 */
function freshAccount(): { email: string; password: string } {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  return { email: `e2e-passkey-${id}@example.com`, password: `E2e-passkey-${id}-password` }
}

interface RegistrationOptions {
  challenge: string
  rp: { id?: string; name: string }
  user: { id: string; name: string; displayName: string }
  pubKeyCredParams: { type: "public-key"; alg: number }[]
  timeout?: number
  excludeCredentials?: { id: string; type: "public-key" }[]
  authenticatorSelection?: AuthenticatorSelectionCriteria
  attestation?: AttestationConveyancePreference
}

/**
 * Create a credential on the browser's virtual authenticator for the options
 * the API issued. Runs in the page: WebAuthn needs the app's origin.
 */
async function createCredential(page: Page, options: RegistrationOptions) {
  return page.evaluate(async (opts) => {
    const fromB64url = (value: string): ArrayBuffer => {
      const b64 = value.replace(/-/g, "+").replace(/_/g, "/")
      const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4)
      return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0)).buffer
    }
    const toB64url = (buffer: ArrayBuffer): string =>
      btoa(String.fromCharCode(...new Uint8Array(buffer)))
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "")

    const credential = (await navigator.credentials.create({
      publicKey: {
        ...opts,
        challenge: fromB64url(opts.challenge),
        user: { ...opts.user, id: fromB64url(opts.user.id) },
        excludeCredentials: (opts.excludeCredentials ?? []).map((c) => ({
          ...c,
          id: fromB64url(c.id),
        })),
      },
    })) as PublicKeyCredential
    const response = credential.response as AuthenticatorAttestationResponse
    return {
      id: credential.id,
      rawId: toB64url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: toB64url(response.clientDataJSON),
        attestationObject: toB64url(response.attestationObject),
        transports: response.getTransports(),
      },
      clientExtensionResults: {},
    }
  }, options)
}

async function addVirtualAuthenticator(cdp: CDPSession): Promise<void> {
  await cdp.send("WebAuthn.enable")
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      protocol: "ctap2",
      transport: "internal",
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
}

/**
 * Wait for a forced-logout arrival to finish clearing the old session: the
 * login screen strips the reason from the address, then signs the browser out
 * and clears the cookie. A virtual authenticator answers instantly, far faster
 * than a person, so without this the ceremony could finish before that
 * cleanup starts.
 */
async function settleForcedLogoutArrival(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/login$/)
  await page.waitForLoadState("networkidle")
}

test("a passkey sign-in lands on the dashboard and stays signed in", async ({
  browser,
  // Requested for its side effect: the practice's owner is whoever signs in
  // first, and that must be the worker's clinician, not this account.
  onboardedUser: _owner,
}) => {
  test.setTimeout(10 * 60 * 1000)
  const { email, password } = freshAccount()
  await createEmulatorUser(email, password)
  const api = new ApiClient(await signInWithPassword(email, password))

  const context = await browser.newContext()
  const page = await context.newPage()
  // Passkey sign-in is a per-deployment switch, off on this stack because it
  // would also send every new account through passkey enrollment. Only this
  // browser's copy of the config needs it, to offer the button.
  await page.route("**/api/config", async (route) => {
    const response = await route.fetch()
    await route.fulfill({ response, json: { ...(await response.json()), passkeysEnabled: true } })
  })
  await addVirtualAuthenticator(await context.newCDPSession(page))

  await page.goto("/login")
  const options = await api.post<RegistrationOptions>("/api/auth/passkey/register/begin", {})
  const credential = await createCredential(page, options)
  await api.post("/api/auth/passkey/register/finish", { credential, device_label: "e2e" })

  const passkeyButton = page.getByRole("button", { name: "Sign in with a passkey" })

  for (let round = 1; round <= ROUNDS; round++) {
    // Odd rounds arrive from a forced logout, which also signs out the
    // session the previous round left; each odd round then signs out from
    // the menu, so the even round after it starts from an ordinary sign-out.
    const afterForcedLogout = round % 2 === 1
    const arrival = afterForcedLogout ? "after a forced logout" : "after signing out"
    await test.step(`round ${round}: passkey sign-in ${arrival}`, async () => {
      if (afterForcedLogout) {
        await page.goto("/login?reason=idle_timeout")
        await settleForcedLogoutArrival(page)
      }

      await passkeyButton.click()
      await page.waitForURL(/\/dashboard/)

      // A full page load, as a reload, a link opened in place or the next
      // visit arrives. The sign-in the browser stored has to be there for it,
      // and the page's first request — the idle controller's session check —
      // has to carry it.
      const sessionCheck = page.waitForResponse((response) =>
        response.url().endsWith("/api/auth/session"),
      )
      // The page's first auth-state tick either re-syncs the session cookie
      // (a signed-in browser) or clears it (one that found nobody stored).
      const cookieSync = page.waitForResponse((response) =>
        /^\/api\/log(in|out)$/.test(new URL(response.url()).pathname),
      )
      await page.reload()
      expect(new URL((await cookieSync).url()).pathname, "first auth tick after a reload").toBe(
        "/api/login",
      )
      expect((await sessionCheck).status(), "session check after a reload").toBe(200)

      // The session cookie has to have survived too: with it gone, every
      // dashboard address redirects to /login.
      await page.goto("/dashboard/settings")
      await expect(page).toHaveURL(/\/dashboard\/settings/)

      if (!afterForcedLogout || round === ROUNDS) return
      await page.getByRole("button", { name: "Open user menu" }).click()
      await page.getByRole("button", { name: "Sign out" }).click()
      await expect(page).toHaveURL(/\/login$/)
    })
  }

  await context.close()
})
