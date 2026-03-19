// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

interface ServiceAccount {
  projectId: string
  privateKey: string
  clientEmail: string
}

const COOKIE_MAX_AGE = 12 * 60 * 60 // 12 hours in seconds
const IS_PRODUCTION = process.env.NODE_ENV === "production"
const DEV_FALLBACK_KEY = "default-dev-key-change-in-production"

function getCookieSignatureKeys(): string[] {
  const key = process.env.AUTH_COOKIE_SIGNATURE_KEY
  if (key) return [key]

  if (IS_PRODUCTION) {
    throw new Error(
      "AUTH_COOKIE_SIGNATURE_KEY must be set in production. " +
        "Without it, auth cookies can be forged. " +
        "Generate a random 32+ character secret and set it as an environment variable."
    )
  }

  return [DEV_FALLBACK_KEY]
}

function getServiceAccount(): ServiceAccount | undefined {
  const projectId = process.env.FIREBASE_PROJECT_ID
  const clientEmail = process.env.FIREBASE_CLIENT_EMAIL
  const privateKey = process.env.FIREBASE_PRIVATE_KEY

  if (!projectId || !clientEmail || !privateKey) {
    return undefined
  }

  return {
    projectId,
    clientEmail,
    privateKey: privateKey.replace(/\\n/g, "\n"),
  }
}

export const authConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY!,
  cookieName: "AuthToken",
  cookieSignatureKeys: getCookieSignatureKeys(),
  cookieSerializeOptions: {
    path: "/",
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    maxAge: COOKIE_MAX_AGE,
  },
  serviceAccount: getServiceAccount(),
  enableMultipleCookies: true,
}

export const loginPath = "/api/login"
export const logoutPath = "/api/logout"
