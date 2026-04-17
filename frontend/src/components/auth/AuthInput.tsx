// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { InputHTMLAttributes } from "react"

interface AuthInputProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string
  label: string
}

export function AuthInput({ id, label, className, ...inputProps }: AuthInputProps) {
  return (
    <div>
      <label
        htmlFor={id}
        className="block text-sm font-medium text-neutral-700 mb-1"
      >
        {label}
      </label>
      <input
        id={id}
        className={
          className ??
          "w-full px-4 py-2 border border-neutral-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        }
        {...inputProps}
      />
    </div>
  )
}
