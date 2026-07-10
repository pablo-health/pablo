// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect } from "react"
import Image from "next/image"
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
        {/* Placeholder illustration — copied from pablo-tie.webp for now;
            swap pablo-error.webp for a purpose-made apologetic Pablo later. */}
        <div className="relative mx-auto h-32 w-32">
          <Image
            src="/pablo-error.webp"
            alt="Pablo, apologizing for the interruption"
            fill
            sizes="128px"
            className="object-contain"
            priority
          />
        </div>
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Pablo dropped the ball
          </h1>
          <p className="text-muted-foreground">
            As Ceremonial Executive Officer (CEO), I apologize on behalf of my
            human &mdash; something on our end broke, not you. Try again, and
            we&apos;ll get you back on track.
          </p>
        </div>

        {error.digest && (
          <div className="rounded-md bg-muted p-4">
            <p className="text-sm text-muted-foreground">
              If it keeps happening, pass this reference along to support:
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
