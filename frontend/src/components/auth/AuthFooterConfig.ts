// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { authFooterConfigExtensions } from "./AuthFooterConfig.extensions"

/**
 * Branding strings for `AuthFooter`. `AuthFooter.tsx` is never forked; a
 * downstream build rebrands by overriding fields in
 * `AuthFooterConfig.extensions.ts`, which are shallow-merged over these
 * defaults below.
 */
export interface AuthFooterConfig {
  label: string
  href: string
  linkText: string
}

const authFooterConfigBase: AuthFooterConfig = {
  label: "Pablo · AGPL-3.0 ·",
  href: "https://github.com/pablo-health/pablo",
  linkText: "github.com/pablo-health/pablo",
}

export const authFooterConfig: AuthFooterConfig = {
  ...authFooterConfigBase,
  ...authFooterConfigExtensions,
}
