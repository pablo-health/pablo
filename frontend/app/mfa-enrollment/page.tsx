// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MFA Enrollment Page
 *
 * Allows users to enroll in TOTP (Time-based One-Time Password) MFA.
 * Displays a QR code for scanning with an authenticator app and verifies enrollment.
 *
 * Note: This page is outside the (dashboard) route group to avoid using
 * the dashboard layout (which would create a redirect loop when checking MFA status).
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { redirect } from "next/navigation"
import { authConfig } from "@/lib/auth-config"
import { MFAEnrollmentForm } from "./MFAEnrollmentForm"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

export default async function MFAEnrollmentPage() {
  // In dev mode, skip MFA entirely
  if (IS_DEV_MODE) {
    redirect("/dashboard")
  }

  // Require authentication
  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) {
    redirect("/login")
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50 p-6">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <header className="mb-8">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-12 h-12 bg-primary-600 rounded-lg flex items-center justify-center">
              <svg
                className="w-6 h-6 text-white"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                />
              </svg>
            </div>
            <div>
              <h1 className="text-3xl font-display font-bold text-neutral-900">
                Enable Two-Factor Authentication
              </h1>
              <p className="text-neutral-600 mt-1">
                Enhanced Security for HIPAA Compliance
              </p>
            </div>
          </div>

          <div className="bg-blue-50 border-l-4 border-blue-600 p-4 rounded-r-lg">
            <div className="flex items-start gap-3">
              <svg
                className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <div>
                <h3 className="text-sm font-semibold text-blue-900 mb-1">
                  Why is this required?
                </h3>
                <p className="text-sm text-blue-800 leading-relaxed">
                  Multi-factor authentication adds an extra layer of security to your account.
                  When accessing Protected Health Information (PHI), MFA helps ensure that only
                  you can access your account, even if your password is compromised.
                </p>
              </div>
            </div>
          </div>
        </header>

        {/* Enrollment Form */}
        <MFAEnrollmentForm />

        {/* Footer */}
        <footer className="mt-8 pt-6 border-t border-neutral-200 text-center text-sm text-neutral-600">
          <p>
            This platform is HIPAA compliant and uses industry-standard encryption
            to protect your data.
          </p>
          <p className="mt-2">
            Questions about MFA?{" "}
            <a
              href="mailto:support@pablo.health"
              className="text-primary-600 hover:text-primary-700 underline"
            >
              Contact Support
            </a>
          </p>
        </footer>
      </div>
    </div>
  )
}
