// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ButtonHTMLAttributes } from "react"

export function AuthOutlineButton({
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={
        className ??
        "w-full bg-white border-2 border-primary-600 text-primary-600 px-6 py-3 rounded-lg font-medium hover:bg-primary-50 active:scale-[0.98] transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
      }
      {...props}
    >
      {children}
    </button>
  )
}
