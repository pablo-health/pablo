// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect } from "react"
import { Button } from "@/components/ui/button"

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error("Application error:", error.name, error.digest ?? "")
  }, [error])

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="mx-auto max-w-md space-y-6 rounded-lg border bg-card p-8 text-center shadow-lg">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight text-destructive">
            Something went wrong
          </h1>
          <p className="text-muted-foreground">
            We apologize for the inconvenience. An unexpected error has occurred.
          </p>
        </div>

        {error.digest && (
          <div className="rounded-md bg-muted p-4">
            <p className="text-sm text-muted-foreground">
              If this problem persists, please contact support with reference ID:
            </p>
            <p className="mt-1 text-xs font-mono text-muted-foreground">
              {error.digest}
            </p>
          </div>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
          <Button onClick={() => reset()} variant="default">
            Try again
          </Button>
          <Button onClick={() => (window.location.href = "/")} variant="outline">
            Go home
          </Button>
        </div>
      </div>
    </div>
  )
}
