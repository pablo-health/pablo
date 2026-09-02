// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { redirect } from "next/navigation"
import { DEFAULT_SETTINGS_ITEM } from "@/components/settings/registry"

/** Settings has no landing page of its own; it opens on the first item. */
export default function SettingsIndexPage() {
  redirect(`/dashboard/settings/${DEFAULT_SETTINGS_ITEM}`)
}
