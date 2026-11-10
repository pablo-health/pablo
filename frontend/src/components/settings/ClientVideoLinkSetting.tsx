// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClientVideoLinkSetting
 *
 * Whether the practice's messages to clients carry a video appointment's link
 * to join. On by default; the practice owner turns it off. Sits in the Video
 * sessions card.
 *
 * Not shown when the deployment sends no link to anybody (`available` false):
 * a switch that changes nothing would say the link goes out when it does not.
 * Not shown before the setting has loaded either, for the same reason.
 *
 * The description names no particular message. No message to a client
 * carries the link yet: reminders are not sent, and the booking message
 * (PABLO-en9h.9) is still to come. So it says what happens when one is sent,
 * which is true today and stays true as each message starts asking
 * `client_join_link`. Never name a message here that does not ask it.
 */

"use client"

import { SavedIndicator, Toggle, useSavedFlash } from "@/components/settings/ui"
import { useClientVideoLinkSetting, useUpdateClientVideoLinkSetting } from "@/hooks/useClientVideoLink"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

export function ClientVideoLinkSetting() {
  const { data: setting } = useClientVideoLinkSetting()
  const update = useUpdateClientVideoLinkSetting()
  const people = usePeopleTerm()
  const { saved, flashSaved } = useSavedFlash()

  if (!setting?.available) return null

  const label = `Send ${people.many} their video link`

  return (
    <div data-testid="client-video-link-setting" className="mt-4 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-5">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">{label}</div>
          <div className="mt-0.5 text-[12.5px] leading-relaxed text-muted-foreground">
            {setting.can_change
              ? `Messages to your ${people.many} about a video session include the link to join.`
              : "Only the practice owner can change this."}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <SavedIndicator saved={saved} />
          <Toggle
            label={label}
            checked={setting.send_clients_video_link}
            disabled={!setting.can_change || update.isPending}
            onChange={(next) => update.mutate(next, { onSuccess: flashSaved })}
          />
        </div>
      </div>
      {update.isError && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          That didn&apos;t save. Try again.
        </p>
      )}
    </div>
  )
}
