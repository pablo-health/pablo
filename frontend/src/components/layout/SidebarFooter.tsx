// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { sidebarFooterConfig as cfg } from "./sidebarFooterConfig"

export function SidebarFooter() {
  return (
    <div className="p-4 border-t border-neutral-200">
      <div className="text-xs text-neutral-500">
        {cfg.label}{" "}
        <a
          href={cfg.href}
          target="_blank"
          rel="noopener noreferrer"
          className={cfg.linkClassName}
        >
          {cfg.linkText}
        </a>
      </div>
    </div>
  )
}
