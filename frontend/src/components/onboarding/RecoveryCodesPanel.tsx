// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Show-once display for one-time recovery codes.
 *
 * The backend returns these in plaintext exactly once at first-passkey
 * enrollment — it keeps only hashes — so this is the user's single
 * chance to save them. We make copy/download easy and require an
 * explicit "I've saved them" acknowledgement before letting the wizard
 * advance.
 */

import { useState } from "react"
import { Check, Copy, Download } from "lucide-react"
import { Button } from "@/components/ui/button"

export function RecoveryCodesPanel({
  codes,
  onContinue,
}: {
  codes: string[]
  onContinue: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)

  const asText = codes.join("\n")

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(asText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard blocked (permissions / insecure context) — the user can
      // still read or download the codes, so swallow it quietly.
    }
  }

  const handleDownload = () => {
    const blob = new Blob([`Pablo recovery codes\n\n${asText}\n`], { type: "text/plain" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "pablo-recovery-codes.txt"
    // Anchor must be in the DOM for the click to register in some browsers;
    // and revoke on the next tick — revoking synchronously can race the
    // browser's blob read and produce an empty download (notably Firefox).
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  return (
    <div className="space-y-5">
      <p className="text-neutral-600">
        Save these recovery codes somewhere safe — a password manager is ideal.
        If you ever lose your passkey, a code lets you sign back in. Each works
        once, and <strong>we can&rsquo;t show them again.</strong>
      </p>

      <ul
        className="grid grid-cols-2 gap-2 rounded-xl border p-4 font-mono text-sm"
        style={{ borderColor: "var(--border)", background: "var(--color-neutral-50)" }}
      >
        {codes.map((code) => (
          <li key={code} className="tracking-wider text-neutral-900">
            {code}
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap gap-3">
        <Button type="button" variant="outline" onClick={handleCopy} className="gap-2">
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button type="button" variant="outline" onClick={handleDownload} className="gap-2">
          <Download className="h-4 w-4" />
          Download
        </Button>
      </div>

      <label className="flex items-start gap-2.5 text-sm text-neutral-700">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
          className="mt-0.5 h-4 w-4"
        />
        <span>I&rsquo;ve saved my recovery codes somewhere safe.</span>
      </label>

      <Button
        type="button"
        onClick={onContinue}
        disabled={!acknowledged}
        className="w-full sm:w-auto"
      >
        Continue
      </Button>
    </div>
  )
}
