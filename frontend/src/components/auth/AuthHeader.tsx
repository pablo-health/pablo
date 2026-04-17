// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ReactNode } from "react"

interface AuthHeaderProps {
  title: string
  subtitle?: ReactNode
  titleColor?: "primary" | "red"
  titleSize?: "3xl" | "4xl"
}

export function AuthHeader({
  title,
  subtitle,
  titleColor = "primary",
  titleSize = "3xl",
}: AuthHeaderProps) {
  const color = titleColor === "red" ? "text-red-600" : "text-primary-600"
  const size = titleSize === "4xl" ? "text-4xl" : "text-3xl"
  return (
    <div className="text-center">
      <h1 className={`${size} font-display font-bold ${color}`}>{title}</h1>
      {subtitle && <p className="mt-3 text-neutral-600">{subtitle}</p>}
    </div>
  )
}
