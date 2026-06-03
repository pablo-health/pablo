// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { authFooterConfig as cfg } from "./AuthFooterConfig"

interface AuthFooterProps {
  spacing?: "default" | "compact"
}

export function AuthFooter({ spacing = "default" }: AuthFooterProps) {
  const wrapper =
    spacing === "compact"
      ? "pt-4 border-t border-neutral-200"
      : "mt-6 pt-6 border-t border-neutral-200"
  return (
    <div className={wrapper}>
      <p className="text-xs text-neutral-500 text-center leading-relaxed">
        {cfg.label}{" "}
        <a
          href={cfg.href}
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-neutral-700"
        >
          {cfg.linkText}
        </a>
      </p>
    </div>
  )
}
