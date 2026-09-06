// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { LucideIcon } from "lucide-react"
import { userMenuExtensions } from "./userMenuExtensions.extensions"

/**
 * User-menu config — the extension point for the account menu in `Header.tsx`.
 *
 * The menu holds what belongs to the person rather than to the practice: who
 * they are signed in as, their theme, signing out. A deployment with more to
 * put there — anything about the account itself rather than the clinical work
 * — adds it through the `userMenuExtensions.extensions.ts` *merge slot* rather
 * than forking the header. Same discipline as `sidebarExtensions.ts`:
 * `Header.tsx` is never forked, and an item added to the base menu here is
 * never shadowed by a stale downstream copy.
 *
 * Deliberately not the sidebar: the sidebar is where the practice's work
 * lives, and an account destination visited a few times a year does not earn
 * a permanent seat next to the daily surfaces — nor should it sit adjacent to
 * them competing for the same words.
 */
export interface UserMenuItem {
  name: string
  href: string
  icon: LucideIcon
}

/** Shape of the merge slot (`userMenuExtensions.extensions.ts`). */
export interface UserMenuExtensions {
  /**
   * Items rendered between the theme control and Sign out, in slot order.
   * Sign out stays last: it is the destructive one, and it should not move
   * under a pointer that is aiming for something above it.
   */
  append: UserMenuItem[]
}

export const userMenuItems: UserMenuItem[] = userMenuExtensions.append
