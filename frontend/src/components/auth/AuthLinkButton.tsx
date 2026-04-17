// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ButtonHTMLAttributes } from "react"

interface AuthLinkButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: "sm" | "base"
  block?: boolean
}

export function AuthLinkButton({
  size = "base",
  block = false,
  className,
  children,
  type = "button",
  ...props
}: AuthLinkButtonProps) {
  const base = "text-primary-600 hover:text-primary-700 hover:underline"
  const sizeClass = size === "sm" ? "text-sm" : ""
  const width = block ? "w-full" : ""
  const merged = [base, sizeClass, width, className].filter(Boolean).join(" ")
  return (
    <button type={type} className={merged} {...props}>
      {children}
    </button>
  )
}
