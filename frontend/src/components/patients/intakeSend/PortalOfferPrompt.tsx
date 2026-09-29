// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { useSavePortalSettings } from "@/hooks/usePortalSettings"
import { portalModuleLabel } from "@/lib/portalModules"

export type PortalAnswer = "on" | "not_now"

interface PortalOfferPromptProps {
  /** The parts this deployment serves, as the settings route lists them. */
  modules: string[]
  /** Called once the answer is saved. */
  onAnswered: (answer: PortalAnswer) => void
}

/**
 * Asked once, the first time a practice adds a client: offer them a portal?
 *
 * A practice decides this for itself rather than finding it on, and the
 * first client is when the question means something. "Not now" is a real
 * answer and is recorded as one, so it is never asked again; Settings is
 * where it changes later. "Yes" goes on to which parts clients get — all of
 * them to start, since that is what the portal is — and turns it on with
 * that choice in one save.
 */
export function PortalOfferPrompt({ modules, onAnswered }: PortalOfferPromptProps) {
  const save = useSavePortalSettings()
  const [choosing, setChoosing] = useState(false)
  const [chosen, setChosen] = useState<string[]>(modules)

  function answer(enabled: boolean) {
    const change = enabled
      ? {
          enabled: true,
          modules: Object.fromEntries(modules.map((name) => [name, chosen.includes(name)])),
        }
      : // Only if nobody at the practice has answered since this screen
        // loaded: a colleague's "on" must not be undone by this "not now".
        { enabled: false, only_if_undecided: true }
    save.mutate(change, { onSuccess: () => onAnswered(enabled ? "on" : "not_now") })
  }

  const failed = save.isError && (
    <p className="text-sm text-red-700" role="alert">
      That didn&rsquo;t save. Try again.
    </p>
  )

  if (!choosing) {
    return (
      <div className="space-y-4" data-testid="portal-offer-prompt">
        <DialogHeader>
          <DialogTitle>Offer your clients a portal?</DialogTitle>
          <DialogDescription>
            Clients sign in between visits to do what you ask of them.
          </DialogDescription>
        </DialogHeader>
        {failed}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={save.isPending} onClick={() => answer(false)}>
            Not now
          </Button>
          <Button type="button" disabled={save.isPending} onClick={() => setChoosing(true)}>
            Yes, set it up
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4" data-testid="portal-offer-choose">
      <DialogHeader>
        <DialogTitle>What can clients do in it?</DialogTitle>
        <DialogDescription>You can change this any time in Settings.</DialogDescription>
      </DialogHeader>
      <ul className="space-y-2">
        {modules.map((name) => {
          const id = `portal-offer-${name}`
          return (
            <li key={name} className="flex items-center gap-2">
              <Checkbox
                id={id}
                checked={chosen.includes(name)}
                onCheckedChange={(on) =>
                  setChosen((current) =>
                    on === true ? [...current, name] : current.filter((n) => n !== name),
                  )
                }
              />
              <Label htmlFor={id} className="font-normal">
                {portalModuleLabel(name)}
              </Label>
            </li>
          )
        })}
      </ul>
      {chosen.length === 0 && (
        <p className="text-sm text-neutral-600">Choose at least one.</p>
      )}
      {failed}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={save.isPending} onClick={() => setChoosing(false)}>
          Back
        </Button>
        <Button
          type="button"
          disabled={save.isPending || chosen.length === 0}
          onClick={() => answer(true)}
        >
          Turn on the portal
        </Button>
      </div>
    </div>
  )
}
