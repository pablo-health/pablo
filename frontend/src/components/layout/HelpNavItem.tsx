// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { CircleHelp } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { supportMailto, useHelpUrl, useSupportEmail } from "@/lib/support"

/**
 * "Help" in the sidebar: opens a small popover with the deployment's help
 * articles and support address, whichever it has configured. Absent when it
 * has neither.
 *
 * A popover rather than a bare mailto link because a mailto does nothing on a
 * machine with no mail app set up, which is common for someone who works
 * entirely in a browser. The popover shows the address so it can be copied.
 */
export function HelpNavItem() {
  const email = useSupportEmail()
  const helpUrl = useHelpUrl()
  if (!email && !helpUrl) return null
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-neutral-700 transition-all duration-200 hover:bg-neutral-100 hover:text-neutral-900"
        >
          <CircleHelp className="h-5 w-5 transition-transform duration-200 group-hover:scale-110" />
          Help
        </button>
      </PopoverTrigger>
      <PopoverContent side="right" align="end" className="space-y-2 text-sm text-neutral-700">
        {helpUrl && (
          <p>
            <a
              href={helpUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-700 underline hover:text-primary-800"
            >
              Help articles
            </a>
          </p>
        )}
        {email && (
          <p>
            Email{" "}
            <a href={supportMailto(email)} className="text-primary-700 underline hover:text-primary-800">
              {email}
            </a>
            .
          </p>
        )}
      </PopoverContent>
    </Popover>
  )
}
