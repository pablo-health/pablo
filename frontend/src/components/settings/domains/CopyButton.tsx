// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef, useState } from "react"
import { Check, Copy } from "lucide-react"

const COPIED_MS = 1500

interface CopyButtonProps {
  text: string
  /** What is copied, for a screen reader: "Copy host for TXT record _pablo-verify". */
  label: string
  /** The text on screen, selected for the practice to copy when the clipboard can't be written. */
  source?: React.RefObject<HTMLElement | null>
}

/** Copies one value and says so beside the icon for a moment. */
export function CopyButton({ text, label, source }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])

  function selectSource() {
    const node = source?.current
    const selection = window.getSelection()
    if (!node || !selection) return
    const range = document.createRange()
    range.selectNodeContents(node)
    selection.removeAllRanges()
    selection.addRange(range)
  }

  async function handleCopy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard")
      await navigator.clipboard.writeText(text)
    } catch {
      selectSource()
      return
    }
    setCopied(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), COPIED_MS)
  }

  return (
    <span className="inline-flex shrink-0 items-center gap-1 font-sans text-muted-foreground">
      <button
        type="button"
        onClick={handleCopy}
        aria-label={label}
        title={label}
        className="rounded p-0.5 hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1"
      >
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      </button>
      <span aria-live="polite" className="text-[11px]">
        {copied ? "Copied" : ""}
      </span>
    </span>
  )
}
