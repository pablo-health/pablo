// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect } from "react"

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error("Global application error:", error.name, error.digest ?? "")
  }, [error])

  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen items-center justify-center bg-gray-50">
          <div className="mx-auto max-w-md space-y-6 rounded-lg border bg-white p-8 text-center shadow-lg">
            <div className="space-y-2">
              <h1 className="text-3xl font-bold tracking-tight text-red-600">
                Critical Error
              </h1>
              <p className="text-gray-600">
                A critical error has occurred. Please try refreshing the page.
              </p>
            </div>

            {error.digest && (
              <div className="rounded-md bg-gray-100 p-4">
                <p className="text-sm text-gray-600">
                  If this problem persists, please contact support with reference ID:
                </p>
                <p className="mt-1 text-xs font-mono text-gray-500">
                  {error.digest}
                </p>
              </div>
            )}

            <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
              <button
                onClick={() => reset()}
                className="rounded-md bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
              >
                Try again
              </button>
              <button
                onClick={() => (window.location.href = "/")}
                className="rounded-md border px-4 py-2 hover:bg-gray-100"
              >
                Go home
              </button>
            </div>
          </div>
        </div>
      </body>
    </html>
  )
}
