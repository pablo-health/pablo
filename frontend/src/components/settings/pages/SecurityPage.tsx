// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { PasskeySettings } from "../PasskeySettings"
import { SecurityLegalRows } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"
import { useConfig } from "@/lib/config"

/**
 * You > Sign-in & security.
 *
 * The agreement rows a managed deployment needs (a BAA and similar) come from
 * the `SecurityLegalRows` slot, so this page never grows a deployment
 * conditional and never has to be forked to add one.
 */
export function SecurityPage() {
  const { passkeysEnabled } = useConfig()

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

      <SecurityLegalRows />
    </>
  )
}
