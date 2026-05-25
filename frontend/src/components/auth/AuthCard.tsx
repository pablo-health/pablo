// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ReactNode } from "react"

interface AuthCardProps {
  children: ReactNode
  className?: string
  /** When provided, renders a two-column layout with a themed brand panel. */
  brandPanel?: ReactNode
  /** Rendered above the card (e.g. the theme switcher). */
  toolbar?: ReactNode
}

export function AuthCard({
  children,
  className,
  brandPanel,
  toolbar,
}: AuthCardProps) {
  const cardClass =
    className ??
    "w-full max-w-md space-y-8 bg-white p-10 shadow-xl border border-neutral-100"

  if (brandPanel) {
    return (
      <div className="auth-canvas flex min-h-screen flex-col lg:flex-row">
        <aside
          className="auth-brand-panel relative hidden lg:flex lg:w-[44%] lg:flex-col lg:justify-between lg:p-12"
          style={{ borderRadius: 0 }}
        >
          {brandPanel}
        </aside>
        <div className="relative flex flex-1 items-center justify-center p-6">
          {toolbar && (
            <div className="absolute right-6 top-6 z-10">{toolbar}</div>
          )}
          <div
            className={cardClass}
            style={{ borderRadius: "var(--auth-radius)" }}
          >
            {children}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="auth-canvas flex min-h-screen items-center justify-center p-6">
      <div className={cardClass} style={{ borderRadius: "var(--auth-radius)" }}>
        {children}
      </div>
    </div>
  )
}
