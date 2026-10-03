// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import type { FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAddPracticeDomain, useDescribePracticeDomain } from "@/hooks/usePracticeDomains"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { ApiError } from "@/lib/api/client"
import type { DomainPurpose } from "@/lib/api/practiceDomains"
import { SegmentedControl } from "../ui"

/** The bare host as typed, for the `www.` label — the server does the real normalising. */
function displayHost(raw: string): string {
  return raw.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/[/.]+$/, "")
}

/** Add a portal or website host. A website can bring its `www.` alias with it. */
export function AddDomainForm() {
  const [domain, setDomain] = useState("")
  const [purpose, setPurpose] = useState<DomainPurpose>("portal")
  // null leaves www. to the server's default for the name; a click makes it the reader's.
  const [wwwChoice, setWwwChoice] = useState<boolean | null>(null)
  const add = useAddPracticeDomain()
  const people = usePeopleTerm()
  const purposes = [
    { value: "portal" as const, label: `${people.One} portal` },
    { value: "site" as const, label: "Website" },
  ]

  const host = displayHost(domain)
  const offersWww = purpose === "site" && host.length > 0 && !host.startsWith("www.")
  // The default is the server's: a bare domain under the Public Suffix List
  // (example.com, example.co.uk) gets www., anything under one does not. The
  // box waits for that answer rather than guess it.
  const described = useDescribePracticeDomain(host, offersWww && wwwChoice === null)
  const includeWww = wwwChoice ?? described?.bare
  const showWww = offersWww && includeWww !== undefined

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!host) return
    // Sent only when the reader changed the box; otherwise the server applies
    // the same default the box showed.
    const wwwOverride = purpose === "site" && offersWww && wwwChoice !== null ? { include_www: wwwChoice } : {}
    add.mutate(
      { domain, purpose, ...wwwOverride },
      {
        onSuccess: () => {
          setDomain("")
          setWwwChoice(null)
        },
      },
    )
  }

  const error =
    add.error instanceof ApiError && add.error.message
      ? add.error.message
      : add.isError
        ? "The domain couldn't be added. Try again."
        : null

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid max-w-md gap-2">
        <Label htmlFor="practice-domain-input">Domain</Label>
        <Input
          id="practice-domain-input"
          value={domain}
          onChange={(e) => {
            setDomain(e.target.value)
            add.reset()
          }}
          placeholder="portal.yourpractice.com"
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      <SegmentedControl label="Use for" value={purpose} onChange={setPurpose} options={purposes} />
      {showWww && (
        <label htmlFor="practice-domain-www" className="flex items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            id="practice-domain-www"
            className="h-4 w-4"
            checked={includeWww}
            onChange={(e) => setWwwChoice(e.target.checked)}
          />
          <span>Also add www.{host}</span>
        </label>
      )}
      <div className="flex items-center gap-3">
        <Button type="submit" size="sm" disabled={!host || add.isPending}>
          {add.isPending ? "Adding..." : "Add domain"}
        </Button>
        {error && (
          <p role="alert" className="text-[12.5px] text-red-700">
            {error}
          </p>
        )}
      </div>
    </form>
  )
}
