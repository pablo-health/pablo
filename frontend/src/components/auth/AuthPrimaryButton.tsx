// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ButtonHTMLAttributes } from "react"

export function AuthPrimaryButton({
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={
        className ??
        "w-full bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
      }
      {...props}
    >
      {children}
    </button>
  )
}
