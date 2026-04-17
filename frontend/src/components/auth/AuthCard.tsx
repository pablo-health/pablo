// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ReactNode } from "react"

interface AuthCardProps {
  children: ReactNode
  className?: string
}

export function AuthCard({ children, className }: AuthCardProps) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
      <div
        className={
          className ??
          "w-full max-w-md space-y-8 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100"
        }
      >
        {children}
      </div>
    </div>
  )
}
