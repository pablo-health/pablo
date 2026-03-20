// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { useId } from "react"
import type { LucideIcon } from "lucide-react"

interface SettingsSectionProps {
  icon: LucideIcon
  title: string
  description: string
  children: React.ReactNode
}

export function SettingsSection({ icon: Icon, title, description, children }: SettingsSectionProps) {
  const titleId = useId()

  return (
    <section aria-labelledby={titleId} className="card p-6">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="h-5 w-5 text-neutral-600" aria-hidden="true" />
        <h2 id={titleId} className="text-lg font-semibold text-neutral-900">
          {title}
        </h2>
      </div>
      <p className="text-sm text-neutral-600 mb-4">{description}</p>
      {children}
    </section>
  )
}
