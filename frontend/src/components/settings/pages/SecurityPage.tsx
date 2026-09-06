// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { PasskeySettings } from "../PasskeySettings"
import { SecurityLegalRows } from "../settingsSlots.extensions"
import { SettingsBadge, SettingsCard, SettingsRow } from "../ui"
import { useSettingsUserStatus } from "../useSettingsPreferences"
import { Button } from "@/components/ui/button"
import { useConfig } from "@/lib/config"

function formatDate(value: string | null): string | null {
  if (!value) return null
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

/**
 * You > Sign-in & security.
 *
 * The agreement rows a managed deployment needs (a BAA and similar) come from
 * the `SecurityLegalRows` slot, so this page never grows a deployment
 * conditional and never has to be forked to add one.
 */
export function SecurityPage() {
  const { passkeysEnabled } = useConfig()
  const { data: status } = useSettingsUserStatus()

  const enrolledOn = formatDate(status?.mfa_enrolled_at ?? null)
  const guideAckOn = formatDate(status?.security_guide_acknowledged_at ?? null)

  return (
    <>
      {passkeysEnabled && (
        <SettingsCard
          title="Passkeys"
          description="Sign in with your fingerprint, face or screen lock. Passkeys cannot be phished."
        >
          <PasskeySettings />
        </SettingsCard>
      )}

      <SettingsCard flush>
        <SettingsRow
          label="Authenticator app"
          description={
            enrolledOn
              ? `Enrolled ${enrolledOn}. Used when no passkey is available.`
              : "Not set up yet. Used when no passkey is available."
          }
        >
          {enrolledOn && <SettingsBadge tone="sage">Enrolled</SettingsBadge>}
          <Button variant="outline" size="sm" asChild>
            <Link href="/mfa-enrollment">Reset</Link>
          </Button>
        </SettingsRow>
        <SettingsRow
          label="Security guide"
          description={
            guideAckOn
              ? `Acknowledged ${guideAckOn}. You'll be asked again when the guide changes.`
              : "Not yet acknowledged."
          }
        >
          <Button variant="ghost" size="sm" disabled>
            Read again
          </Button>
        </SettingsRow>
      </SettingsCard>

      <SecurityLegalRows />
    </>
  )
}
