// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

export function AuthDivider() {
  return (
    <div className="relative">
      <div className="absolute inset-0 flex items-center">
        <div className="w-full border-t border-neutral-300"></div>
      </div>
      <div className="relative flex justify-center text-sm">
        <span className="px-2 bg-white text-neutral-500">or</span>
      </div>
    </div>
  )
}
