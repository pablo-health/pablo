// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, type InputHTMLAttributes } from "react"
import { Eye, EyeOff } from "lucide-react"

interface AuthInputProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string
  label: string
}

export function AuthInput({
  id,
  label,
  className,
  type,
  ...inputProps
}: AuthInputProps) {
  const [revealed, setRevealed] = useState(false)
  const isPassword = type === "password"
  // Swap to a text input while revealed so the value is legible.
  const effectiveType = isPassword && revealed ? "text" : type

  const inputClassName =
    className ??
    `w-full px-4 py-2 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent${
      isPassword ? " pr-11" : ""
    }`

  return (
    <div>
      <label
        htmlFor={id}
        className="block text-sm font-medium text-neutral-700 mb-1"
      >
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={effectiveType}
          className={inputClassName}
          {...inputProps}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setRevealed((v) => !v)}
            aria-label={revealed ? "Hide password" : "Show password"}
            aria-pressed={revealed}
            // Keep the toggle out of the tab order so it doesn't sit
            // between the password field and the submit button.
            tabIndex={-1}
            className="absolute inset-y-0 right-0 flex items-center px-3 text-neutral-400 hover:text-neutral-600 focus:outline-none focus-visible:text-neutral-600"
          >
            {revealed ? (
              <EyeOff className="h-5 w-5" aria-hidden="true" />
            ) : (
              <Eye className="h-5 w-5" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
    </div>
  )
}
