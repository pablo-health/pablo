// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ReactNode } from "react"

interface AuthFeedbackProps {
  variant: "error" | "success"
  children: ReactNode
  padding?: "3" | "4"
}

export function AuthFeedback({ variant, children, padding = "3" }: AuthFeedbackProps) {
  const pad = padding === "4" ? "p-4" : "p-3"
  if (variant === "error") {
    return (
      <div className={`${pad} bg-red-50 border border-red-200 rounded-lg`}>
        <p className="text-sm text-red-600">{children}</p>
      </div>
    )
  }
  const successText = padding === "4" ? "text-green-800" : "text-green-700"
  return (
    <div className={`${pad} bg-green-50 border border-green-200 rounded-lg`}>
      <p className={`text-sm ${successText}`}>{children}</p>
    </div>
  )
}
