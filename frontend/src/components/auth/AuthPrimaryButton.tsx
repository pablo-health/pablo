// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ButtonHTMLAttributes } from "react"

export function AuthPrimaryButton({
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={className ?? "auth-primary"} {...props}>
      {children}
    </button>
  )
}
