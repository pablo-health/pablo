// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useBookingButton, useSetBookingButton } from "@/hooks/useBookingButton"
import { useBookingLinks } from "@/hooks/useBookingLinks"
import { ApiError } from "@/lib/api/client"
import type { BookingButton } from "@/lib/api/bookingButton"
import type { BookingLink } from "@/types/bookingLinks"
import { SavedIndicator, SettingsCard, SettingsRow, Toggle, useSavedFlash } from "../ui"

const OUTSIDE = "outside"

interface BookSessionCardProps {
  canManage: boolean
  /** Whether this deployment publishes websites; the website switch is hidden when it doesn't. */
  showWebsite: boolean
}

/**
 * The practice's "Book a session" button on its website and in its client
 * portal (backend/app/portal/booking_button.py). It goes to one of the
 * practice's booking links or to a booking page the practice already uses
 * elsewhere — linking there is a good outcome, so the copy offers it plainly
 * and never as a lesser choice. On the website it fills in a link the
 * practice marks on its own pages (backend/app/sites/book_button.py); in the
 * portal it is the header's button. Off until the practice turns a surface on; a
 * booking link that is turned off hides the button everywhere, which the card
 * says when it is the case.
 */
export function BookSessionCard({ canManage, showWebsite }: BookSessionCardProps) {
  const { data: button } = useBookingButton()
  const { data: links } = useBookingLinks()
  // Held here, above the form, so the form starting over on a save does not
  // take the "Saved" confirmation with it.
  const save = useSetBookingButton()
  const flash = useSavedFlash()
  if (!button || !links) return null
  // Keyed on what is saved, so a save that lands resets the form to it.
  const key = [button.booking_link_id, button.outside_url, button.on_website, button.in_portal].join("|")
  return (
    <BookSessionForm
      key={key}
      saved={button}
      links={links.data}
      save={save}
      flash={flash}
      canManage={canManage}
      showWebsite={showWebsite && button.website_available}
    />
  )
}

interface BookSessionFormProps extends BookSessionCardProps {
  saved: BookingButton
  links: BookingLink[]
  save: ReturnType<typeof useSetBookingButton>
  flash: ReturnType<typeof useSavedFlash>
}

function BookSessionForm({ saved, links, save, flash, canManage, showWebsite }: BookSessionFormProps) {
  const [target, setTarget] = useState(saved.outside_url !== null ? OUTSIDE : (saved.booking_link_id ?? ""))
  const [outsideUrl, setOutsideUrl] = useState(saved.outside_url ?? "")
  const [onWebsite, setOnWebsite] = useState(saved.on_website)
  const [inPortal, setInPortal] = useState(saved.in_portal)

  const chosenLink = links.find((link) => link.id === target)
  const hasTarget = target === OUTSIDE ? outsideUrl.trim() !== "" : chosenLink !== undefined
  const disabled = !canManage || save.isPending
  const error = save.error instanceof ApiError && save.error.message ? save.error.message : save.error ? "That change couldn't be saved. Try again." : null

  const submit = () =>
    save.mutate(
      {
        booking_link_id: target !== OUTSIDE && target ? target : null,
        outside_url: target === OUTSIDE ? outsideUrl.trim() : null,
        on_website: hasTarget && onWebsite,
        in_portal: hasTarget && inPortal,
      },
      { onSuccess: flash.flashSaved },
    )

  return (
    <SettingsCard title="Book a session" description="A button clients can use to book a time with you." flush>
      <SettingsRow label="Where it goes">
        <Select value={target || undefined} onValueChange={setTarget} disabled={disabled}>
          <SelectTrigger aria-label="Where it goes" className="w-64">
            <SelectValue placeholder="Choose a booking page" />
          </SelectTrigger>
          <SelectContent>
            {links.map((link) => (
              <SelectItem key={link.id} value={link.id}>
                {link.title} · /book/{link.slug}
              </SelectItem>
            ))}
            <SelectItem value={OUTSIDE}>A booking page you use elsewhere</SelectItem>
          </SelectContent>
        </Select>
      </SettingsRow>
      {target === OUTSIDE && (
        <SettingsRow label="Booking page address" nested>
          <Input
            aria-label="Booking page address"
            type="url"
            inputMode="url"
            placeholder="https://"
            className="w-64"
            value={outsideUrl}
            disabled={disabled}
            onChange={(event) => setOutsideUrl(event.target.value)}
          />
        </SettingsRow>
      )}
      {chosenLink && !chosenLink.is_active && (
        <p data-testid="book-session-link-off" className="border-t border-border px-[22px] py-3 text-[12.5px] text-muted-foreground">
          This booking link is off, so the button is hidden.
        </p>
      )}
      {showWebsite && (
        <SettingsRow
          label="On your website"
          description={
            <>
              Fills in each link on your pages marked <code>data-pablo=&quot;book&quot;</code>.
            </>
          }
        >
          <Toggle label="On your website" checked={onWebsite} disabled={disabled || !hasTarget} onChange={setOnWebsite} />
        </SettingsRow>
      )}
      <SettingsRow label="In your client portal">
        <Toggle label="In your client portal" checked={inPortal} disabled={disabled || !hasTarget} onChange={setInPortal} />
      </SettingsRow>
      <div className="flex items-center justify-end gap-3 border-t border-border px-[22px] py-3">
        {error && (
          <p role="alert" className="mr-auto text-[12.5px] text-red-700">
            {error}
          </p>
        )}
        <SavedIndicator saved={flash.saved} />
        <Button size="sm" onClick={submit} disabled={disabled}>
          Save
        </Button>
      </div>
    </SettingsCard>
  )
}
