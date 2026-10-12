// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ErrorScreen } from "@/components/errors/ErrorScreen"

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return <ErrorScreen error={error} onRetry={reset} logLabel="Application error" />
}
