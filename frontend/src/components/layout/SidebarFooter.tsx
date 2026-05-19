// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

export function SidebarFooter() {
  return (
    <div className="p-4 border-t border-neutral-200">
      <div className="text-xs text-neutral-500">
        Pablo · AGPL-3.0 ·{" "}
        <a
          href="https://github.com/pablo-health/pablo"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-neutral-700 break-all"
        >
          github.com/pablo-health/pablo
        </a>
      </div>
    </div>
  )
}
